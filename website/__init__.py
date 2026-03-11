from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from sqlalchemy.exc import SQLAlchemyError
import pytz
import logging
import atexit

# Initialize extensions globally
db       = SQLAlchemy()
socketio = SocketIO(cors_allowed_origins="*", async_mode='threading')
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Manila'))

# Grace period — devices stay ON for this many minutes after a schedule ends
GRACE_PERIOD_MINUTES = 15

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app():
    """Application factory pattern"""
    app = Flask(__name__)

    # Configuration
    app.config['SECRET_KEY'] = 'RAMMS'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://root:@localhost/flask_users'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle':  300,
    }

    # Initialize extensions with app
    db.init_app(app)
    socketio.init_app(app)

    # Create database tables
    with app.app_context():
        from .models import User, Device, Room, Schedule, Usage
        db.create_all()
        logger.info("Database tables created/verified")

    # Register blueprints
    from .views import views
    from .auth import auth
    app.register_blueprint(auth, url_prefix='/')
    app.register_blueprint(views, url_prefix='/')

    # Start background scheduler
    if not scheduler.running:
        scheduler.add_job(
            func          = lambda: _run_with_context(app),
            trigger       = 'interval',
            seconds       = 60,
            id            = 'device_state_updater',
            max_instances = 1,      # prevent overlapping runs
            coalesce      = True,   # skip missed runs instead of catching up
            replace_existing = True
        )
        scheduler.start()
        logger.info("Background scheduler started")

    # Shutdown scheduler when app closes
    atexit.register(lambda: scheduler.shutdown())

    return app


def _run_with_context(app):
    """Runs update_device_states() inside the Flask app context."""
    with app.app_context():
        update_device_states()


def update_device_states():
    """
    Automatically turn devices ON/OFF based on schedules.
    Runs every 60 seconds via background scheduler.

    Logic per room:
      - Active schedule right now?        → devices ON
      - No active, ended within 15 min?   → grace period, keep devices ON
      - Grace period expired?             → devices OFF
      - No schedules today / all future?  → devices OFF
    """
    manila_tz  = pytz.timezone('Asia/Manila')
    now        = datetime.now(manila_tz)
    today_name = now.strftime('%A')
    now_time   = now.time().replace(tzinfo=None)
    grace      = timedelta(minutes=GRACE_PERIOD_MINUTES)

    logger.info(f"[Scheduler] Running check at {now.strftime('%Y-%m-%d %H:%M:%S')} ({today_name})")

    try:
        from .models import Room, Device, Schedule

        rooms = Room.query.all()
        if not rooms:
            logger.info("[Scheduler] No rooms found, skipping.")
            return

        for room in rooms:
            schedules_today = Schedule.query.filter_by(
                room_id=room.id,
                day=today_name
            ).all()

            active_schedule = None
            most_recent_end = None

            for sched in schedules_today:
                try:
                    start = (datetime.min + sched.start_time).time() \
                            if isinstance(sched.start_time, timedelta) \
                            else sched.start_time

                    end   = (datetime.min + sched.end_time).time() \
                            if isinstance(sched.end_time, timedelta) \
                            else sched.end_time

                except Exception as e:
                    logger.warning(f"[Scheduler] Bad time value in schedule id={sched.id}: {e}")
                    continue

                # Currently inside a schedule window
                if start <= now_time < end:
                    active_schedule = sched
                    break

                # Already ended — track the latest end time for grace period
                if now_time >= end:
                    if most_recent_end is None or end > most_recent_end:
                        most_recent_end = end

            # ── Determine target state ───────────────────────────────────────

            if active_schedule:
                target_state = 1
                label  = getattr(active_schedule, 'subject', None) or f"id={active_schedule.id}"
                reason = f"active schedule '{label}' ({active_schedule.start_time}–{active_schedule.end_time})"

            elif most_recent_end is not None:
                end_dt  = manila_tz.localize(datetime.combine(now.date(), most_recent_end))
                elapsed = now - end_dt

                if elapsed <= grace:
                    remaining = int((grace - elapsed).total_seconds() / 60)
                    logger.info(
                        f"[Scheduler] Room '{room.name}': grace period active, "
                        f"{remaining} min remaining — keeping devices ON."
                    )
                    continue  # skip state change, devices stay as-is

                else:
                    target_state = 0
                    reason = (
                        f"no active schedule, grace period expired "
                        f"({GRACE_PERIOD_MINUTES} min after {most_recent_end})"
                    )

            else:
                target_state = 0
                reason = "no schedules for today or all are upcoming"

            # ── Apply state to all devices in room ───────────────────────────

            devices = Device.query.filter_by(room_id=room.id).all()
            changed = [d for d in devices if d.state != target_state]

            for device in changed:
                device.state = target_state

            if changed:
                db.session.commit()
                logger.info(
                    f"[Scheduler] Room '{room.name}': "
                    f"set {len(changed)} device(s) to {'ON' if target_state else 'OFF'} "
                    f"— reason: {reason}"
                )
                broadcast_schedule_updates(room.id, target_state, reason, devices)
            else:
                logger.info(f"[Scheduler] Room '{room.name}': no state changes needed.")

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"[Scheduler] Database error: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"[Scheduler] Unexpected error: {e}", exc_info=True)


def broadcast_schedule_updates(room_id, target_state, reason, devices):
    """
    Broadcast device state changes to all connected clients via WebSocket.
    """
    try:
        socketio.emit(
            'schedule_device_update',
            {
                'room_id':      room_id,
                'target_state': target_state,
                'reason':       reason,
                'updated_by':   'scheduler',
                'timestamp':    datetime.now(pytz.timezone('Asia/Manila')).isoformat(),
                'devices': [
                    {'id': d.id, 'name': d.name, 'state': d.state}
                    for d in devices
                ]
            },
            room=f"room_{room_id}"
        )
        logger.info(f"[Scheduler] Broadcasted update to room_{room_id}")

    except Exception as e:
        logger.error(f"[Scheduler] Error broadcasting update: {e}", exc_info=True)


def trigger_device_update_now(app):
    """
    Manually trigger device state update (useful for testing).
    Can be called from Flask shell or an admin endpoint.
    """
    logger.info("[Scheduler] Manual device update triggered.")
    with app.app_context():
        update_device_states()