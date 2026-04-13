from datetime import datetime, date, time, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from .models import Device, Room, Schedule, Usage, User, DeviceUsage, Sensor
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, socketio
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from collections import defaultdict
from functools import wraps
from flask_socketio import emit, join_room, leave_room
from sqlalchemy import func
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
    
# ============ ESP FIRMWARE API============


@auth.route('/api/esp32/push', methods=['POST'])
def esp32_push():
    """
    Receives readings from all 6 PZEM sensors on the ESP32.
 
    Expected JSON body:
    {
        "readings": [
            {
                "serial_number": "SN-0001",
                "pzem_address":  "0x01",
                "voltage":       220.4,
                "current":       1.23,
                "power":         271.0,
                "energy_kwh":    0.045,
                "frequency":     60.0,
                "power_factor":  0.98,
                "sensor_error":  false
            },
            { ... },  up to 6 entries
        ]
    }
    """
    try:
        data = request.get_json()
 
        if not data or 'readings' not in data:
            return jsonify(success=False, error="Missing 'readings' array"), 400
 
        readings = data['readings']
 
        if not isinstance(readings, list) or len(readings) == 0:
            return jsonify(success=False, error="'readings' must be a non-empty list"), 400
 
        saved   = []
        skipped = []
        now     = datetime.now()
 
        for r in readings:
            serial_number = r.get('serial_number', '').strip()
            voltage       = r.get('voltage')
            current       = r.get('current')
            power         = r.get('power')
            energy_kwh    = r.get('energy_kwh')
            frequency     = r.get('frequency')
            power_factor  = r.get('power_factor')
            sensor_error  = r.get('sensor_error', False)
 
            # ── Validate required fields ────────────────────────────────────
 
            if not serial_number:
                skipped.append({'serial_number': serial_number,
                                'reason': 'Missing serial_number'})
                continue
 
            if energy_kwh is None:
                skipped.append({'serial_number': serial_number,
                                'reason': 'Missing energy_kwh'})
                continue
 
            # ── Look up sensor by serial number ─────────────────────────────
 
            sensor = Sensor.query.filter_by(serial_number=serial_number).first()
            if not sensor:
                skipped.append({'serial_number': serial_number,
                                'reason': f'No sensor with serial_number={serial_number} in DB'})
                continue
 
            # ── Log sensor errors ────────────────────────────────────────────
 
            if sensor_error:
                import logging
                logging.warning(
                    f"[ESP32] sensor_error=True for {serial_number} "
                    f"at {now.isoformat()} — saving zeros to keep data stream alive."
                )
 
            # ── Save to DeviceUsage ──────────────────────────────────────────
 
            usage = DeviceUsage(
                device_id    = sensor.device_id,
                sensor_id    = sensor.id,
                voltage      = float(voltage)     if voltage     is not None else 0.0,
                current      = float(current)     if current     is not None else 0.0,
                power        = float(power)       if power       is not None else 0.0,
                energy_kwh   = float(energy_kwh),
                reading_time = now
            )
            db.session.add(usage)
 
            saved.append({
                'serial_number': serial_number,
                'pzem_address':  r.get('pzem_address', '?'),
                'device_id':     sensor.device_id,
                'sensor_id':     sensor.id,
                'energy_kwh':    float(energy_kwh),
                'power':         float(power)        if power        is not None else 0.0,
                'frequency':     float(frequency)    if frequency    is not None else None,
                'power_factor':  float(power_factor) if power_factor is not None else None,
                'sensor_error':  sensor_error,
            })
 
        db.session.commit()
 
        # ── Broadcast each saved reading via Socket.IO ───────────────────────
 
        for s in saved:
            device = Device.query.get(s['device_id'])
            if device and device.room_id:
                socketio.emit(
                    'usage_updated',
                    {
                        'room_id':      device.room_id,
                        'device_id':    device.id,
                        'device_name':  device.name,
                        'kwh_used':     s['energy_kwh'],
                        'power_w':      s.get('power'),
                        'frequency':    s.get('frequency'),
                        'power_factor': s.get('power_factor'),
                        'sensor_error': s.get('sensor_error', False),
                        'timestamp':    now.isoformat(),
                        'source':       'ESP32'
                    },
                    room=f"room_{device.room_id}"
                )
 
        return jsonify(
            success       = True,
            saved_count   = len(saved),
            skipped_count = len(skipped),
            saved         = saved,
            skipped       = skipped,
            timestamp     = now.isoformat()
        ), 200
 
    except SQLAlchemyError as e:
        db.session.rollback()
        return jsonify(success=False, error='Database error', detail=str(e)), 500
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500
 
 
@auth.route('/api/esp32/states', methods=['GET'])
def esp32_states():
    """
    ESP32 polls this every cycle to get ON/OFF relay state per device,
    identified by serial number.
 
    Query param (optional):
      ?serial_numbers=SN-0001,SN-0002,SN-0003,SN-0004,SN-0005,SN-0006
 
    If omitted, returns states for ALL sensors in the DB.
 
    Response:
    {
        "success": true,
        "states": {
            "SN-0001": 1,
            "SN-0002": 0,
            "SN-0003": 1,
            "SN-0004": 1,
            "SN-0005": 0,
            "SN-0006": 1
        }
    }
    """
    try:
        serial_param = request.args.get('serial_numbers', '').strip()
 
        if serial_param:
            serials = [s.strip() for s in serial_param.split(',') if s.strip()]
            sensors = Sensor.query.filter(Sensor.serial_number.in_(serials)).all()
        else:
            sensors = Sensor.query.all()
 
        states = {}
        for sensor in sensors:
            device = Device.query.get(sensor.device_id)
            if device:
                states[sensor.serial_number] = device.state  # 1=ON, 0=OFF
 
        return jsonify(success=True, states=states), 200
 
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500
 
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
    Expects data format: { 'device_id': 1, 'kwh_used': 0.5, 'timestamp': '...' }
    """
    device_id = data.get('device_id')
    kwh_used = data.get('kwh_used')
    timestamp = data.get('timestamp')
    
    if not device_id:
        return

    # Find which room this device belongs to
    device = Device.query.get(device_id)
    
    if device and device.room_id:
        # Broadcast to that specific room's channel
        socketio.emit(
            'usage_updated',
            {
                'room_id': device.room_id,
                'device_id': device.id,
                'device_name': device.name,
                'kwh_used': kwh_used,
                'timestamp': timestamp,
                'message': 'New usage data available'
            },
            room=f"room_{device.room_id}",
            broadcast=True
        )
        print(f"Broadcasted usage for Device {device.id} in Room {device.room_id}")

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

        # --- NEW LOGIC: Sum energy from DeviceUsage for today ---
        today_start = datetime.combine(date.today(), time.min)
        today_end = datetime.combine(date.today(), time.max)
        
        # SQL: SELECT SUM(energy_kwh) FROM device_usages WHERE reading_time BETWEEN today_start AND today_end
        total_energy = db.session.query(func.sum(DeviceUsage.energy_kwh)).filter(
            DeviceUsage.reading_time >= today_start,
            DeviceUsage.reading_time <= today_end
        ).scalar()
        
        # If no data, result is None, so set to 0
        energy_today = total_energy if total_energy else 0

        return render_template(
            "home.html",
            room_count=room_count,
            active_devices=active_devices,
            schedules_today=schedules_today,
            energy_today=round(energy_today, 2)
        )
    except Exception as e:
        print(f"Dashboard Error: {e}")
        flash('Could not load dashboard data.', 'error')
        return render_template(
            "home.html",
            room_count='0',
            active_devices='0',
            schedules_today='0',
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
@auth.route('/add_device', methods=['POST'])
@admin_required
def add_device():
    room_id      = request.form.get('room_id', '').strip()
    device_name  = request.form.get('device_name', '').strip()
    serial_number = request.form.get('serial_number', '').strip()

    # ── Validation ──────────────────────────────────────────────────────────

    # 1. room_id must be present and numeric
    if not room_id or not room_id.isdigit():
        flash('Invalid room selected.', 'error')
        return redirect(url_for('auth.control_devices'))

    room_id = int(room_id)

    # 2. Room must exist in DB
    room = Room.query.get(room_id)
    if not room:
        flash('Room not found.', 'error')
        return redirect(url_for('auth.control_devices'))

    # 3. Device name must be provided and within length limits
    if not device_name:
        flash('Device name is required.', 'error')
        return redirect(url_for('auth.control_devices'))

    if len(device_name) < 2:
        flash('Device name must be at least 2 characters.', 'error')
        return redirect(url_for('auth.control_devices'))

    if len(device_name) > 150:
        flash('Device name must be under 150 characters.', 'error')
        return redirect(url_for('auth.control_devices'))

    # 4. Room must not already have 9 devices (UI enforces this but backend should too)
    if len(room.devices) >= 9:
        flash(f'Room "{room.name}" has reached the maximum of 9 devices.', 'error')
        return redirect(url_for('auth.control_devices'))

    # 5. Serial number must be provided
    if not serial_number:
        flash('Sensor serial number is required.', 'error')
        return redirect(url_for('auth.control_devices'))

    if len(serial_number) > 100:
        flash('Serial number must be under 100 characters.', 'error')
        return redirect(url_for('auth.control_devices'))

    # 6. Serial number must be unique across all sensors
    existing_sensor = Sensor.query.filter_by(serial_number=serial_number).first()
    if existing_sensor:
        flash(f'Serial number "{serial_number}" is already assigned to another device.', 'error')
        return redirect(url_for('auth.control_devices'))

    # 7. Device name must be unique within the same room
    existing_device = Device.query.filter_by(name=device_name, room_id=room_id).first()
    if existing_device:
        flash(f'A device named "{device_name}" already exists in room "{room.name}".', 'error')
        return redirect(url_for('auth.control_devices'))

    # ── Create Device + Sensor ───────────────────────────────────────────────

    try:
        # 1. Create Device
        new_device = Device(name=device_name, state=0, room_id=room_id)
        db.session.add(new_device)
        db.session.flush()  # generates new_device.id before creating sensor

        # 2. Always create a linked Sensor
        new_sensor = Sensor(
            name          = f"{device_name} Sensor",
            serial_number = serial_number,
            device_id     = new_device.id
        )
        db.session.add(new_sensor)
        db.session.commit()

        flash(f'Device "{device_name}" and sensor "{serial_number}" added successfully!', 'success')

    except IntegrityError:
        db.session.rollback()
        flash('A device or sensor with that name/serial already exists.', 'error')
    except SQLAlchemyError as e:
        db.session.rollback()
        flash(f'Database error: Failed to add device.', 'error')

    return redirect(url_for('auth.control_devices'))
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
@auth.route('/api/usage/rooms')
@login_required_redirect
def api_usage_rooms():
    """Returns list of all rooms for the dashboard room selector."""
    rooms = Room.query.all()
    return jsonify({
        'rooms': [{'id': r.id, 'name': r.name} for r in rooms]
    })


@auth.route('/api/room_devices/<int:room_id>')
@login_required_redirect
def api_room_devices(room_id):
    """Returns all devices in a room for the dashboard device selector."""
    devices = Device.query.filter_by(room_id=room_id).all()
    return jsonify({
        'devices': [{'id': d.id, 'name': d.name} for d in devices]
    })
@auth.route('/statistics/<int:room_id>')
@login_required_redirect
def statistics(room_id):
    room = Room.query.get_or_404(room_id)
    
    # 1. Get all devices in this room
    devices = Device.query.filter_by(room_id=room.id).all()
    device_ids = [d.id for d in devices]
    
    # Simple list for the dropdown/info
    devices_serializable = [{'id': d.id, 'name': d.name} for d in devices]

    if not device_ids:
        # If room has no devices, return empty charts
        empty_data = {"daily": [], "weekly": [], "monthly": [], "hourly": []}
        return render_template('statistics.html', usage_data=empty_data, room=room, devices=devices_serializable)

    # 2. Fetch all usage records for these devices from the last 30 days
    # We join DeviceUsage with Device to filter by the list of IDs
    cutoff_date = datetime.now() - timedelta(days=30)
    
    raw_usages = DeviceUsage.query.filter(
        DeviceUsage.device_id.in_(device_ids),
        DeviceUsage.reading_time >= cutoff_date
    ).all()

    # 3. Process Data using Python (easiest way to group)
    daily_dict = defaultdict(float)
    weekly_dict = defaultdict(float)
    monthly_dict = defaultdict(float)
    hourly_dict = defaultdict(float)

    for record in raw_usages:
        kwh = record.energy_kwh
        dt = record.reading_time # This is a datetime object
        
        # Create keys for grouping
        day_key = dt.strftime('%Y-%m-%d')
        week_key = dt.strftime('%Y-%W')
        month_key = dt.strftime('%Y-%m')
        
        # For hourly: Only show hours from the last 24h to keep chart clean
        if dt >= datetime.now() - timedelta(hours=24):
            hour_key = dt.strftime('%Y-%m-%d %H:00')
            hourly_dict[hour_key] += kwh

        daily_dict[day_key] += kwh
        weekly_dict[week_key] += kwh
        monthly_dict[month_key] += kwh

    # 4. Format for Chart.js (List of dicts)
    usage_data = {
        "daily": [{"date": k, "kwh_used": v} for k, v in sorted(daily_dict.items())],
        "weekly": [{"date": k, "kwh_used": v} for k, v in sorted(weekly_dict.items())],
        "monthly": [{"date": k, "kwh_used": v} for k, v in sorted(monthly_dict.items())],
        "hourly": [{"hour": k, "kwh_used": v} for k, v in sorted(hourly_dict.items())]
    }

    return render_template('statistics.html', usage_data=usage_data, room=room, devices=devices_serializable)
@auth.route('/api/usage/<int:room_id>')
@login_required_redirect
def api_usage(room_id):
    # 1. Identify devices
    devices = Device.query.filter_by(room_id=room_id).all()
    device_ids = [d.id for d in devices]

    usage_response = {
        "daily": {"labels": [], "values": []},
        "weekly": {"labels": [], "values": []},
        "monthly": {"labels": [], "values": []},
        "hourly": {"labels": [], "values": []}
    }

    if not device_ids:
        return jsonify(usage_response)

    # 2. Fetch data (Last 30 days default)
    raw_usages = DeviceUsage.query.filter(
        DeviceUsage.device_id.in_(device_ids)
    ).all()

    # 3. Grouping Logic
    daily = defaultdict(float)
    weekly = defaultdict(float)
    monthly = defaultdict(float)
    hourly = defaultdict(float)

    for u in raw_usages:
        dt = u.reading_time
        kwh = u.energy_kwh
        
        daily[dt.strftime("%Y-%m-%d")] += kwh
        weekly[dt.strftime("%Y-%W")] += kwh
        monthly[dt.strftime("%Y-%m")] += kwh
        hourly[dt.strftime("%Y-%m-%d %H:00")] += kwh

    # 4. Populate Response
    def populate(target_key, source_dict):
        for k, v in sorted(source_dict.items()):
            usage_response[target_key]["labels"].append(k)
            usage_response[target_key]["values"].append(v)

    populate("daily", daily)
    populate("weekly", weekly)
    populate("monthly", monthly)
    populate("hourly", hourly)

    return jsonify(usage_response)

@auth.route('/api/device_usage/<int:device_id>')
@login_required_redirect
def api_device_usage(device_id):
    """API to get usage history for a SINGLE device"""
    device = Device.query.get_or_404(device_id)
    
    # Verify ownership/room access if needed here
    
    usage_response = {
        "daily": {"labels": [], "values": []},
        "weekly": {"labels": [], "values": []},
        "monthly": {"labels": [], "values": []},
        "hourly": {"labels": [], "values": []}
    }

    # Fetch data (Last 30 days)
    # We filter specifically by this device_id
    raw_usages = DeviceUsage.query.filter(
        DeviceUsage.device_id == device_id
    ).order_by(DeviceUsage.reading_time.asc()).all()

    # Reuse the same grouping logic as api_usage
    daily = defaultdict(float)
    weekly = defaultdict(float)
    monthly = defaultdict(float)
    hourly = defaultdict(float)

    for u in raw_usages:
        dt = u.reading_time
        kwh = u.energy_kwh
        
        daily[dt.strftime("%Y-%m-%d")] += kwh
        weekly[dt.strftime("%Y-%W")] += kwh
        monthly[dt.strftime("%Y-%m")] += kwh
        hourly[dt.strftime("%Y-%m-%d %H:00")] += kwh

    # Populate Response
    def populate(target_key, source_dict):
        for k, v in sorted(source_dict.items()):
            usage_response[target_key]["labels"].append(k)
            usage_response[target_key]["values"].append(v)

    populate("daily", daily)
    populate("weekly", weekly)
    populate("monthly", monthly)
    populate("hourly", hourly)

    return jsonify(usage_response)