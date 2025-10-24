from datetime import datetime, date, time, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from .models import Device, Room, Schedule, Usage, User
from werkzeug.security import generate_password_hash, check_password_hash
from . import db
from sqlalchemy.exc import IntegrityError
from flask import jsonify

auth = Blueprint('auth', __name__)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form.get('password')
        
        # Retrieve user from the database
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            session['username'] = user.username
            session['role'] = user.role
            return redirect(url_for('auth.home'))
        else:
            flash('Invalid username or password', 'error')

    return render_template("login.html")

@auth.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

@auth.route('/sign-up', methods=['GET', 'POST'])
def sign_up():
    if request.method == 'POST':
        username = request.form['username']
        role = request.form['role']
        name = request.form['name']
        password = request.form['password']
        
        # Check for an existing username
        if User.query.filter_by(username=username).first():
            flash('Username already exists. Please choose a different one.', 'error')
            return redirect(url_for('auth.manage_users'))

        try:
            new_user = User(username=username, role=role, name=name)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! You can now log in.', 'success')
            return redirect(url_for('auth.manage_users'))
        except IntegrityError:
            db.session.rollback()
            flash('Error registering user. Please try again.', 'error')
        
    return render_template("signup.html")

@auth.route('/home')
def home():
    if 'username' not in session:
        return redirect(url_for('auth.login'))
    return render_template("home.html")

@auth.route('/manage-users')
def manage_users():
    if 'username' not in session:
        return redirect(url_for('auth.login'))
    users = User.query.all()
    return render_template('manage.html', users=users)

from datetime import datetime

@auth.route('/add_schedule', methods=['POST'])
def add_schedule():
    day = request.form['day']
    # Use a placeholder date (e.g., 2000-01-01) to create datetime objects
    start_time = datetime.strptime(f"2000-01-01 {request.form['start_time']}", '%Y-%m-%d %H:%M')
    end_time = datetime.strptime(f"2000-01-01 {request.form['end_time']}", '%Y-%m-%d %H:%M')
    subject = request.form['subject']
    teacher = request.form['teacher']

    # Check for time conflicts
    existing_schedules = Schedule.query.filter_by(day=day).all()
    for schedule in existing_schedules:
        if (start_time < schedule.end_time and end_time > schedule.start_time):
            flash("This time slot is already taken!", "error")
            return redirect(url_for('auth.schedule'))

    # Add new schedule if no conflicts
    new_schedule = Schedule(day=day, start_time=start_time, end_time=end_time, subject=subject, teacher=teacher)
    db.session.add(new_schedule)
    db.session.commit()
    flash("Schedule added successfully!", "success")
    return redirect(url_for('auth.schedule'))

@auth.route('/remove-schedule/<int:schedule_id>', methods=['POST'])
def remove_schedule(schedule_id):
    schedule = Schedule.query.get(schedule_id)
    if schedule:
        db.session.delete(schedule)
        db.session.commit()
        flash("Schedule removed successfully!", "success")
    else:
        flash("Schedule not found.", "error")
    return redirect(url_for('auth.schedule'))
@auth.route('/get_schedules', methods=['GET'])
def get_schedules():
    schedules = Schedule.query.all()
    events = []
    for schedule in schedules:
        event = {
            'id': schedule.id,
            'title': schedule.subject,
            'start': f"{schedule.day}T{schedule.start_time.strftime('%H:%M')}:00",  # Adjust date format as needed
            'end': f"{schedule.day}T{schedule.end_time.strftime('%H:%M')}:00"  # Adjust date format as needed
        }
        events.append(event)
    return jsonify(events)

@auth.route('/schedule')
def schedule():
    if 'username' not in session:
        return redirect(url_for('auth.login'))
    schedules = Schedule.query.all()
    return render_template('schedule.html', schedules=schedules)
    
@auth.route('/delete-user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    try:
        # Directly filter by ID and delete
        db.session.query(User).filter(User.id == user_id).delete()
        db.session.commit()
        flash('User deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Failed to delete user.', 'error')
    return redirect(url_for('auth.manage_users'))

@auth.route('/control_devices')
def control_devices():
    if 'username' not in session:
        return redirect(url_for('auth.login'))
    # Use SQLAlchemy to get all rooms and devices
    rooms = Room.query.all()
    return render_template('control_devices.html', rooms=rooms)

@auth.route('/add_room', methods=['POST'])
def add_room():
    room_name = request.form['room_name']
    
    # Check if a room with the same name already exists
    existing_room = Room.query.filter_by(name=room_name).first()
    if existing_room:
        flash("Room with this name already exists.", "error")
        return redirect(url_for('auth.control_devices'))
    
    # Proceed to add if no duplicates found
    new_room = Room(name=room_name)
    db.session.add(new_room)
    db.session.commit()
    flash("Room added successfully!", "success")
    
    return redirect(url_for('auth.control_devices'))

@auth.route('/add_device', methods=['POST'])
def add_device():
    room_id = request.form['room_id']
    device_name = request.form['device_name']
    new_device = Device(name=device_name, state=0, room_id=room_id)
    db.session.add(new_device)
    db.session.commit()
    flash('Device added successfully!', 'success')
    return redirect(url_for('auth.control_devices'))

@auth.route('/toggle_device', methods=['POST'])
def toggle_device():
    device_id = request.json['device_id']
    new_state = request.json['state']
    
    device = Device.query.get(device_id)
    if device:
        device.state = new_state
        db.session.commit()
        return jsonify(success=True)
    return jsonify(success=False), 404

@auth.route('/toggle_all', methods=['POST'])
def toggle_all():
    room_id = request.json['room_id']
    new_state = request.json['state']
    
    devices = Device.query.filter_by(room_id=room_id).all()
    for device in devices:
        device.state = new_state
    db.session.commit()
    return jsonify(success=True)

@auth.route('/delete_room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    room = Room.query.get(room_id)
    if room:
        db.session.delete(room)
        db.session.commit()
        flash('Room and its devices deleted successfully!', 'success')
    else:
        flash('Room not found.', 'error')
    return redirect(url_for('auth.control_devices'))

@auth.route('/delete_device/<int:device_id>', methods=['POST'])
def delete_device(device_id):
    device = Device.query.get(device_id)
    if device:
        db.session.delete(device)
        db.session.commit()
        flash('Device deleted successfully!', 'success')
    else:
        flash('Device not found.', 'error')
    return redirect(url_for('auth.control_devices'))

@auth.route('/statistics/<int:room_id>')
def statistics(room_id):
    # Fetch the room details
    room = Room.query.get(room_id)
    
    if not room:
        return "Room not found", 404  # Handle the case where the room does not exist
    
    # Query usage data for daily, weekly, and monthly usage
    daily_usage = db.session.query(Usage).filter(
        Usage.room_id == room_id,
        Usage.usage_date >= date.today() - timedelta(days=1)
    ).all()
    
    weekly_usage = db.session.query(Usage).filter(
        Usage.room_id == room_id,
        Usage.usage_date >= date.today() - timedelta(days=7)
    ).all()
    
    monthly_usage = db.session.query(Usage).filter(
        Usage.room_id == room_id,
        Usage.usage_date >= date.today() - timedelta(days=30)
    ).all()
    
    # Convert query results to list of dictionaries
    def usage_to_dict(usage_data):
        return [{"date": u.usage_date.strftime('%Y-%m-%d'), "kwh_used": u.kwh_used} for u in usage_data]
    
    usage_data = {
        "daily": usage_to_dict(daily_usage),
        "weekly": usage_to_dict(weekly_usage),
        "monthly": usage_to_dict(monthly_usage)
    }
    
    return render_template('statistics.html', usage_data=usage_data, room=room)
@auth.route('/api/usage/<int:room_id>')
def api_usage(room_id):
    usage_data = {
        "daily": {
            "labels": [],
            "values": []
        },
        "weekly": {
            "labels": [],
            "values": []
        },
        "monthly": {
            "labels": [],
            "values": []
        }
    }

    # Get all data for the specified room_id
    usages = Usage.query.filter(Usage.room_id == room_id).all()

    # Populate usage data
    for usage in usages:
        # Assuming usage.usage_date is a date object
        usage_date = str(usage.usage_date)

        # Daily data
        usage_data["daily"]["labels"].append(usage_date)
        usage_data["daily"]["values"].append(usage.kwh_used)

        # Weekly data (group by week)
        week_label = usage.usage_date.strftime("%Y-%W")  # Year-Week number
        if week_label not in usage_data["weekly"]["labels"]:
            usage_data["weekly"]["labels"].append(week_label)
            usage_data["weekly"]["values"].append(usage.kwh_used)
        else:
            index = usage_data["weekly"]["labels"].index(week_label)
            usage_data["weekly"]["values"][index] += usage.kwh_used

        # Monthly data (group by month)
        month_label = usage.usage_date.strftime("%Y-%m")  # Year-Month
        if month_label not in usage_data["monthly"]["labels"]:
            usage_data["monthly"]["labels"].append(month_label)
            usage_data["monthly"]["values"].append(usage.kwh_used)
        else:
            index = usage_data["monthly"]["labels"].index(month_label)
            usage_data["monthly"]["values"][index] += usage.kwh_used



    return jsonify(usage_data)
@auth.route('/api/usage_data')
def usage_data():
    return jsonify({
        'daily': usage_data['daily'],
        'weekly': usage_data['weekly'],
        'monthly': usage_data['monthly']
    })