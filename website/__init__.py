from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, time
import pytz
import logging
import atexit

# Initialize extensions globally
db = SQLAlchemy()
socketio = SocketIO(cors_allowed_origins="*", async_mode='threading')
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Manila'))

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
        'pool_recycle': 300,
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
            func=update_device_states,
            trigger="interval",
            minutes=1,
            id='device_state_updater',
            args=[app],
            replace_existing=True
        )
        scheduler.start()
        logger.info("Background scheduler started")
    
    # Shutdown scheduler when app closes
    atexit.register(lambda: scheduler.shutdown())
    
    return app

def update_device_states(app):
    """
    Automatically turn devices ON/OFF based on schedules.
    Runs every minute via background scheduler.
    """
    with app.app_context():
        try:
            from .models import Device, Schedule
            
            # Get current time in Asia/Manila timezone
            manila_tz = pytz.timezone('Asia/Manila')
            current_datetime = datetime.now(manila_tz)
            current_time = current_datetime.time()
            current_day = current_datetime.strftime('%A')
            
            logger.info(f"Running scheduler check at {current_datetime.strftime('%Y-%m-%d %H:%M:%S')} ({current_day})")
            
            # Query all schedules for today
            schedules = Schedule.query.filter_by(day=current_day).all()
            
            if not schedules:
                logger.info(f"No schedules found for {current_day}")
                return
            
            # Track devices that should be ON based on active schedules
            devices_to_turn_on = set()
            active_schedules = []
            
            for schedule in schedules:
                # --- FIX: Convert timedelta from DB to time for comparison ---
                # The mysql-connector driver returns TIME columns as timedelta objects.
                dummy_date = datetime.min
                start_time = (dummy_date + schedule.start_time).time()
                end_time = (dummy_date + schedule.end_time).time()
                
                # Check if current time falls within this schedule
                if start_time <= current_time < end_time: # Use '<' for end_time to turn off exactly on the minute
                    active_schedules.append(schedule)
                    
                    # Get all devices in this room
                    devices = Device.query.filter_by(room_id=schedule.room_id).all()
                    devices_to_turn_on.update(device.id for device in devices)
            
            logger.info(f"Active schedules: {len(active_schedules)}, Devices to turn ON: {len(devices_to_turn_on)}")
            
            # Fetch all devices
            all_devices = Device.query.all()
            
            # Track changes for broadcasting
            devices_updated = []
            
            for device in all_devices:
                old_state = device.state
                new_state = None
                
                # Turn ON if device is in active schedule and currently OFF
                if device.id in devices_to_turn_on and device.state == 0:
                    device.state = 1
                    new_state = 1
                    logger.info(f"Turning ON device {device.id} ({device.name}) in room {device.room_id}")
                
                # Turn OFF if device is NOT in active schedule and currently ON
                elif device.id not in devices_to_turn_on and device.state == 1:
                    device.state = 0
                    new_state = 0
                    logger.info(f"Turning OFF device {device.id} ({device.name}) in room {device.room_id}")
                
                # Track changes for WebSocket broadcast
                if new_state is not None:
                    devices_updated.append({
                        'device_id': device.id,
                        'device_name': device.name,
                        'room_id': device.room_id,
                        'old_state': old_state,
                        'new_state': new_state
                    })
            
            # Commit all changes
            if devices_updated:
                db.session.commit()
                logger.info(f"Updated {len(devices_updated)} devices based on schedule")
                
                # Broadcast updates via WebSocket
                broadcast_schedule_updates(devices_updated)
            else:
                logger.info("No device state changes needed")
        
        except Exception as e:
            logger.error(f"Error in update_device_states: {str(e)}", exc_info=True)
            db.session.rollback()

def broadcast_schedule_updates(devices_updated):
    """
    Broadcast device state changes to all connected clients via WebSocket.
    """
    try:
        # Group devices by room for efficient broadcasting
        rooms_affected = {}
        for device in devices_updated:
            room_id = device['room_id']
            if room_id not in rooms_affected:
                rooms_affected[room_id] = []
            rooms_affected[room_id].append(device)
        
        # Broadcast to each room
        for room_id, devices in rooms_affected.items():
            socketio.emit(
                'schedule_device_update',
                {
                    'room_id': room_id,
                    'devices': devices,
                    'timestamp': datetime.now(pytz.timezone('Asia/Manila')).isoformat(),
                    'updated_by': 'scheduler'
                },
                room=f"room_{room_id}"
            )
            logger.info(f"Broadcasted schedule update to room_{room_id}")
    
    except Exception as e:
        logger.error(f"Error broadcasting schedule updates: {str(e)}", exc_info=True)

# Optional: Manual trigger for testing
def trigger_device_update_now(app):
    """
    Manually trigger device state update (useful for testing).
    Can be called from Flask shell or admin endpoint.
    """
    logger.info("Manual device update triggered")
    update_device_states(app)

