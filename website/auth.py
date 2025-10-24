from datetime import datetime, date, time, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from .models import Device, Room, Schedule, Usage, User
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, socketio
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from collections import defaultdict
from functools import wraps
from flask_socketio import emit, join_room, leave_room

auth = Blueprint('auth', __name__)



@socketio.on('connect')
def handle_connect():
    if 'username' not in session:
        return False
    print(f"User {session.get('username')} connected")
    return True

@socketio.on('join_room')
def on_join_room(data):
    if 'username' not in session:
        return {'success': False, 'error': 'Unauthorized'}
    
    room_id = data.get('room_id')
    if not room_id:
        return {'success': False, 'error': 'Missing room_id'}
    
    room = Room.query.get(room_id)
    if not room:
        return {'success': False, 'error': 'Room not found'}
    
    join_room(f"room_{room_id}")
    return {'success': True}

@socketio.on('leave_room')
def on_leave_room(data):
    room_id = data.get('room_id')
    if room_id:
        leave_room(f"room_{room_id}")
    return {'success': True}

@socketio.on('disconnect')
def handle_disconnect():
    print(f"User {session.get('username')} disconnected")
# ============ DECORATORS & HELPERS ============

def login_required_redirect(f):
    """Redirect to login if not authenticated"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('auth.login'))
        if session.get('role') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('auth.home'))
        return f(*args, **kwargs)
    return decorated_function

def device_ownership(f):
    """Verify user can access device (JSON routes)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return jsonify(success=False, error="Unauthorized"), 401
        
        device_id = request.json.get('device_id')
        if not device_id:
            return jsonify(success=False, error="Missing device_id"), 400
        
        device = Device.query.get(device_id)
        if not device:
            return jsonify(success=False, error="Device not found"), 404
        
        request.device = device
        return f(*args, **kwargs)
    return decorated_function

def room_ownership(f):
    """Verify user can access room (JSON routes)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return jsonify(success=False, error="Unauthorized"), 401
        
        room_id = request.json.get('room_id')
        if not room_id:
            return jsonify(success=False, error="Missing room_id"), 400
        
        room = Room.query.get(room_id)
        if not room:
            return jsonify(success=False, error="Room not found"), 404
        
        request.room = room
        return f(*args, **kwargs)
    return decorated_function


@socketio.on('usage_data_added')
def handle_usage_data(data):
    """
    Broadcast when new usage data is added.
    Call this from your data insertion logic or PHP endpoint.
    """
    room_id = data.get('room_id')
    kwh_used = data.get('kwh_used')
    timestamp = data.get('timestamp')
    
    if room_id:
        socketio.emit(
            'usage_updated',
            {
                'room_id': room_id,
                'kwh_used': kwh_used,
                'timestamp': timestamp,
                'message': 'New usage data available'
            },
            room=f"room_{room_id}",
            broadcast=True
        )
        print(f"Broadcasted usage update for room {room_id}: {kwh_used} kWh")


# ============ AUTHENTICATION ROUTES ============

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Username and password are required.', 'error')
            return render_template("login.html")
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            session['username'] = user.username
            session['user_id'] = user.id
            session['role'] = user.role
            flash(f'Welcome, {user.name}!', 'success')
            return redirect(url_for('auth.home'))
        else:
            flash('Invalid username or password.', 'error')

    return render_template("login.html")

@auth.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

@auth.route('/sign-up', methods=['GET', 'POST'])
def sign_up():
    # Determine redirect URL for validation errors
    # If admin is logged in, they are likely on the manage_users page
    error_redirect_url = url_for('auth.manage_users') if session.get('role') == 'admin' else url_for('auth.sign_up')

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        role = request.form.get('role', 'user').strip() # Default to 'user' role
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '')
        
        # Validation
        if not all([username, name, password]):
            flash('Username, name, and password are required.', 'error')
            return redirect(error_redirect_url)
        
        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'error')
            return redirect(error_redirect_url)
        
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return redirect(error_redirect_url)
        
        if role not in ['admin', 'manager', 'user']:
            flash('Invalid role selected.', 'error')
            return redirect(error_redirect_url)

        if User.query.filter_by(username=username).first():
            flash('Username already exists. Please choose a different one.', 'error')
            return redirect(error_redirect_url)

        try:
            new_user = User(username=username, role=role, name=name)
            new_user.set_password(password) # Assumes you have this method in your User model
            db.session.add(new_user)
            db.session.commit()

            # Smart redirect based on who is creating the account
            if session.get('role') == 'admin':
                flash(f'User "{username}" has been created successfully!', 'success')
                return redirect(url_for('auth.manage_users'))
            else:
                flash('Registration successful! You can now log in.', 'success')
                return redirect(url_for('auth.login'))

        except (IntegrityError, SQLAlchemyError) as e:
            db.session.rollback()
            flash('A database error occurred. Please try again.', 'error')
        except Exception as e:
            db.session.rollback()
            flash('An unexpected error occurred.', 'error')
        
        return redirect(error_redirect_url)

    # For GET request, show the public sign-up form
    return render_template("signup.html")

# ============ USER MANAGEMENT ============

@auth.route('/home')
@login_required_redirect
def home():
    """Render the main dashboard with summary statistics."""
    try:
        room_count = Room.query.count()
        active_devices = Device.query.filter_by(state=1).count()
        
        # Get today's day name (e.g., 'Wednesday')
        today_str = datetime.now().strftime('%A')
        schedules_today = Schedule.query.filter_by(day=today_str).count()

        # Calculate today's energy usage
        today_date = date.today()
        todays_usage_records = Usage.query.filter_by(usage_date=today_date).all()
        energy_today = sum(record.kwh_used for record in todays_usage_records)

        return render_template(
            "home.html",
            room_count=room_count,
            active_devices=active_devices,
            schedules_today=schedules_today,
            energy_today=round(energy_today, 2)
        )
    except Exception as e:
        flash('Could not load dashboard data.', 'error')
        return render_template(
            "home.html",
            room_count='Error',
            active_devices='Error',
            schedules_today='Error',
            energy_today='0'
        )


@auth.route('/manage-users')
@admin_required
def manage_users():
    users = User.query.all()
    return render_template('manage.html', users=users)

@auth.route('/delete-user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    # Prevent self-deletion
    if user_id == session.get('user_id'):
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('auth.manage_users'))
    
    try:
        user = User.query.get(user_id)
        if not user:
            flash('User not found.', 'error')
        else:
            db.session.delete(user)
            db.session.commit()
            flash('User deleted successfully.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Failed to delete user.', 'error')
    
    return redirect(url_for('auth.manage_users'))

# ============ DEVICE CONTROL ============

@auth.route('/control_devices')
@login_required_redirect
def control_devices():
    rooms = Room.query.all()
    return render_template('control_devices.html', rooms=rooms)

@auth.route('/add_room', methods=['POST'])
@admin_required
def add_room():
    room_name = request.form.get('room_name', '').strip()
    
    if not room_name or len(room_name) < 2:
        flash('Room name must be at least 2 characters.', 'error')
        return redirect(url_for('auth.control_devices'))
    
    if Room.query.filter_by(name=room_name).first():
        flash('Room with this name already exists.', 'error')
        return redirect(url_for('auth.control_devices'))
    
    try:
        new_room = Room(name=room_name)
        db.session.add(new_room)
        db.session.commit()
        flash('Room added successfully!', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Failed to add room.', 'error')
    
    return redirect(url_for('auth.control_devices'))

@auth.route('/add_device', methods=['POST'])
@admin_required
def add_device():
    room_id = request.form.get('room_id')
    device_name = request.form.get('device_name', '').strip()
    
    if not device_name or len(device_name) < 2:
        flash('Device name must be at least 2 characters.', 'error')
        return redirect(url_for('auth.control_devices'))
    
    room = Room.query.get(room_id)
    if not room:
        flash('Room not found.', 'error')
        return redirect(url_for('auth.control_devices'))
    
    if len(room.devices) >= 9:
        flash('Maximum device limit (9) reached for this room.', 'error')
        return redirect(url_for('auth.control_devices'))
    
    try:
        new_device = Device(name=device_name, state=0, room_id=room_id)
        db.session.add(new_device)
        db.session.commit()
        flash('Device added successfully!', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Failed to add device.', 'error')
    
    return redirect(url_for('auth.control_devices'))

@auth.route('/delete_room/<int:room_id>', methods=['POST'])
@admin_required
def delete_room(room_id):
    try:
        room = Room.query.get(room_id)
        if not room:
            flash('Room not found.', 'error')
        else:
            db.session.delete(room)
            db.session.commit()
            flash('Room and its devices deleted successfully!', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Failed to delete room.', 'error')
    
    return redirect(url_for('auth.control_devices'))

@auth.route('/delete_device/<int:device_id>', methods=['POST'])
@admin_required
def delete_device(device_id):
    try:
        device = Device.query.get(device_id)
        if not device:
            flash('Device not found.', 'error')
        else:
            db.session.delete(device)
            db.session.commit()
            flash('Device deleted successfully!', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Failed to delete device.', 'error')
    
    return redirect(url_for('auth.control_devices'))

# ============ DEVICE TOGGLE (API) ============

@auth.route('/toggle_device', methods=['POST'])
@device_ownership
def toggle_device():
    """Toggle a single device state"""
    try:
        new_state = request.json.get('state')
        
        if new_state not in (0, 1, True, False):
            return jsonify(success=False, error="Invalid state value"), 400
        
        device = request.device
        old_state = device.state
        device.state = int(new_state)
        room_id = device.room_id
        
        db.session.commit()
        socketio.emit(
            'device_updated',
            {
                'device_id': device.id,
                'device_name': device.name,
                'state': device.state,
                'room_id': room_id,
                'updated_by': session.get('username'),
                'timestamp': datetime.now().isoformat()
            },
            room=f"room_{room_id}"
        )
        
        return jsonify(
            success=True,
            message=f"Device '{device.name}' turned {'on' if new_state else 'off'}",
            device_id=device.id,
            old_state=old_state,
            new_state=device.state
        )
    
    except KeyError as e:
        return jsonify(success=False, error=f"Missing field: {str(e)}"), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(success=False, error="Database error"), 500
    except Exception as e:
        return jsonify(success=False, error="An error occurred"), 500

@auth.route('/toggle_all', methods=['POST'])
@room_ownership
def toggle_all():
    """Toggle all devices in a room"""
    try:
        new_state = request.json.get('state')
        
        if new_state not in (0, 1, True, False):
            return jsonify(success=False, error="Invalid state value"), 400
        
        room = request.room
        devices = room.devices
        
        if not devices:
            return jsonify(success=True, message="No devices in room", devices_updated=0)
        updated_devices = []
        for device in devices:
            device.state = int(new_state)
            updated_devices.append({
                'device_id': device.id,
                'device_name': device.name,
                'state': device.state
            })
        
        db.session.commit()

        socketio.emit(
            'devices_toggled_all',
            {
                'room_id': room.id,
                'devices': updated_devices,
                'new_state': int(new_state),
                'updated_by': session.get('username'),
                'timestamp': datetime.now().isoformat()
            },
            room=f"room_{room.id}"
        )
        
        return jsonify(
            success=True,
            message=f"Toggled {len(devices)} device(s)",
            room_id=room.id,
            devices_updated=len(devices),
            new_state=int(new_state)
        )
    
    except KeyError as e:
        return jsonify(success=False, error=f"Missing field: {str(e)}"), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(success=False, error="Database error"), 500
    except Exception as e:
        return jsonify(success=False, error="An error occurred"), 500

# ============ SCHEDULING ============

@auth.route('/schedule')
@login_required_redirect
def schedule():
    rooms = Room.query.all()
    schedules = Schedule.query.all()
    return render_template('schedule.html', rooms=rooms, schedules=schedules)

@auth.route('/add_schedule', methods=['POST'])
@admin_required
def add_schedule():
    try:
        day = request.form.get('day', '').strip()
        room_id = request.form.get('room_id')
        start_time_str = request.form.get('start_time', '').strip()
        end_time_str = request.form.get('end_time', '').strip()
        subject = request.form.get('subject', '').strip()
        teacher = request.form.get('teacher', '').strip()
        
        # Validation
        if not all([day, room_id, start_time_str, end_time_str, subject, teacher]):
            flash('All fields are required.', 'error')
            return redirect(url_for('auth.schedule'))
        
        room = Room.query.get(room_id)
        if not room:
            flash('Room not found.', 'error')
            return redirect(url_for('auth.schedule'))
        
        # --- FIX: Convert form strings to time objects, not datetime objects ---
        start_time = datetime.strptime(start_time_str, '%H:%M').time()
        end_time = datetime.strptime(end_time_str, '%H:%M').time()
        
        if start_time >= end_time:
            flash('End time must be after start time.', 'error')
            return redirect(url_for('auth.schedule'))
        
        # Check conflicts using the corrected time objects
        conflicts = Schedule.query.filter_by(day=day, room_id=room_id).filter(
            (Schedule.start_time < end_time) & (Schedule.end_time > start_time)
        ).first()
        
        if conflicts:
            flash(f'This time slot conflicts with "{conflicts.subject}" taught by {conflicts.teacher}.', 'error')
            return redirect(url_for('auth.schedule'))
        
        new_schedule = Schedule(
            day=day, start_time=start_time, end_time=end_time,
            subject=subject, teacher=teacher, room_id=room_id
        )
        db.session.add(new_schedule)
        db.session.commit()
        flash('Schedule added successfully!', 'success')
    
    except ValueError:
        flash('Invalid time format. Please use HH:MM.', 'error')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Database error: Failed to add schedule.', 'error')
    
    return redirect(url_for('auth.schedule'))


@auth.route('/remove-schedule/<int:schedule_id>', methods=['POST'])
@admin_required
def remove_schedule(schedule_id):
    try:
        schedule = Schedule.query.get(schedule_id)
        if not schedule:
            flash('Schedule not found.', 'error')
        else:
            db.session.delete(schedule)
            db.session.commit()
            flash('Schedule removed successfully!', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Failed to remove schedule.', 'error')
    
    return redirect(url_for('auth.schedule'))

@auth.route('/get_schedules', methods=['GET'])
def get_schedules():
    try:
        schedules = Schedule.query.all()
        events = [
            {
                'id': s.id,
                'title': s.subject,
                'start': f"{s.day}T{s.start_time.strftime('%H:%M')}:00",
                'end': f"{s.day}T{s.end_time.strftime('%H:%M')}:00"
            }
            for s in schedules
        ]
        return jsonify(events)
    except Exception as e:
        return jsonify([]), 500

# ============ STATISTICS ============

@auth.route('/statistics/<int:room_id>')
@login_required_redirect
def statistics(room_id):
    room = Room.query.get_or_404(room_id)
    devices = Device.query.filter_by(room_id=room.id).all()
    devices_serializable = [{'id': d.id, 'name': d.name} for d in devices]
    
    # --- FIX: Helper function to consistently convert timedelta to time ---
    def to_time(delta):
        if isinstance(delta, time):
            return delta
        return (datetime.min + delta).time()

    daily_usage = Usage.query.filter(
        Usage.room_id == room_id,
        Usage.usage_date >= date.today() - timedelta(days=1)
    ).all()

    weekly_usage = Usage.query.filter(
        Usage.room_id == room_id,
        Usage.usage_date >= date.today() - timedelta(days=7)
    ).all()

    monthly_usage = Usage.query.filter(
        Usage.room_id == room_id,
        Usage.usage_date >= date.today() - timedelta(days=30)
    ).all()

    hourly_usage_raw = Usage.query.filter(
        Usage.room_id == room_id,
        Usage.usage_date >= date.today() - timedelta(days=1)
    ).all()

    hourly_dict = defaultdict(float)
    for usage in hourly_usage_raw:
        # --- FIX: Use the new helper function for conversion ---
        usage_time_obj = to_time(usage.usage_time)
        dt = datetime.combine(usage.usage_date, usage_time_obj)
        hour_label = dt.strftime('%Y-%m-%d %H:00')
        hourly_dict[hour_label] += usage.kwh_used

    def usage_to_dict(usage_data):
        return [{"date": u.usage_date.strftime('%Y-%m-%d'), "kwh_used": u.kwh_used} for u in usage_data]

    usage_data = {
        "daily": usage_to_dict(daily_usage),
        "weekly": usage_to_dict(weekly_usage),
        "monthly": usage_to_dict(monthly_usage),
        "hourly": [{"hour": h, "kwh_used": k} for h, k in sorted(hourly_dict.items())]
    }

    return render_template('statistics.html', usage_data=usage_data, room=room, devices=devices_serializable)

@auth.route('/api/usage/<int:room_id>')
@login_required_redirect
def api_usage(room_id):
    usage_data = {
        "daily": {"labels": [], "values": []},
        "weekly": {"labels": [], "values": []},
        "monthly": {"labels": [], "values": []},
        "hourly": {"labels": [], "values": []},
        "minute": {"labels": [], "values": []}
    }

    usages = Usage.query.filter(Usage.room_id == room_id).all()
    
    # --- FIX: Helper function to consistently convert timedelta to time ---
    def to_time(delta):
        if isinstance(delta, time):
            return delta
        return (datetime.min + delta).time()

    daily_usage = {}
    hourly_usage = {}
    minute_usage = {}
    weekly_usage = {}
    monthly_usage = {}
    
    for usage in usages:
        usage_date = str(usage.usage_date)
        # --- FIX: Use helper to convert timedelta before combining ---
        usage_time_obj = to_time(usage.usage_time)
        usage_datetime = datetime.combine(usage.usage_date, usage_time_obj)
        
        daily_usage[usage_date] = daily_usage.get(usage_date, 0) + usage.kwh_used
        hourly_usage[usage_datetime.strftime("%Y-%m-%d %H:00")] = hourly_usage.get(usage_datetime.strftime("%Y-%m-%d %H:00"), 0) + usage.kwh_used
        minute_usage[usage_datetime.strftime("%Y-%m-%d %H:%M")] = minute_usage.get(usage_datetime.strftime("%Y-%m-%d %H:%M"), 0) + usage.kwh_used
        weekly_usage[usage_datetime.strftime("%Y-%W")] = weekly_usage.get(usage_datetime.strftime("%Y-%W"), 0) + usage.kwh_used
        monthly_usage[usage_datetime.strftime("%Y-%m")] = monthly_usage.get(usage_datetime.strftime("%Y-%m"), 0) + usage.kwh_used
    
    for k, v in sorted(daily_usage.items()):
        usage_data["daily"]["labels"].append(k)
        usage_data["daily"]["values"].append(v)
    for k, v in sorted(hourly_usage.items()):
        usage_data["hourly"]["labels"].append(k)
        usage_data["hourly"]["values"].append(v)
    for k, v in sorted(minute_usage.items()):
        usage_data["minute"]["labels"].append(k)
        usage_data["minute"]["values"].append(v)
    for k, v in sorted(weekly_usage.items()):
        usage_data["weekly"]["labels"].append(k)
        usage_data["weekly"]["values"].append(v)
    for k, v in sorted(monthly_usage.items()):
        usage_data["monthly"]["labels"].append(k)
        usage_data["monthly"]["values"].append(v)

    return jsonify(usage_data)

