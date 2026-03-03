from werkzeug.security import generate_password_hash, check_password_hash
from . import db
from datetime import datetime

class User(db.Model):
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    role = db.Column(db.String(50), nullable=False)
    password = db.Column(db.String(255), nullable=False)  # Use a hash for storing passwords
    name = db.Column(db.String(100), nullable=False)
    
    def __repr__(self):
        return f'<User {self.username}>'
    
    def set_password(self, secret):
        """Hashes the password and stores it."""
        self.password = generate_password_hash(secret)  # Set the hashed password here
    
    def check_password(self, secret):
        """Checks if the provided password matches the stored hashed password."""
        return check_password_hash(self.password, secret)


class Room(db.Model):
    __tablename__ = 'rooms'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    
    # Relationships to other models
    devices = db.relationship('Device', back_populates='room', cascade="all, delete-orphan", lazy=True)
    usages = db.relationship('Usage', back_populates='room', cascade="all, delete-orphan", lazy=True)
    schedules = db.relationship('Schedule', back_populates='room', lazy=True)

    def __repr__(self):
        return f"<Room(id={self.id}, name='{self.name}')>"


class Schedule(db.Model):
    __tablename__ = 'schedules'

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.String(10), nullable=False)  # e.g., 'Monday', 'Tuesday'
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    teacher = db.Column(db.String(100), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)

    room = db.relationship('Room', back_populates='schedules')

    def __repr__(self):
        return f"<Schedule {self.subject} on {self.day} from {self.start_time} to {self.end_time}>"


class Device(db.Model):
    __tablename__ = 'devices'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150))
    state = db.Column(db.Integer, default=0) # 0=Off, 1=On
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    
    room = db.relationship('Room', back_populates='devices')

    # One Device has One Sensor
    sensor = db.relationship('Sensor', back_populates='device', uselist=False, cascade="all, delete-orphan")
    
    # One Device has Many Usages
    device_usages = db.relationship('DeviceUsage', back_populates='device', cascade="all, delete-orphan")


class Usage(db.Model):
    __tablename__ = 'usages'
    
    id = db.Column(db.Integer, primary_key=True)
    usage_date = db.Column(db.Date, nullable=False)
    usage_time = db.Column(db.Time, nullable=False)  # New column to store the time
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)  # Foreign key to Room
    kwh_used = db.Column(db.Float, nullable=False)  # Store the kWh used for that day
    
    room = db.relationship("Room", back_populates="usages")

    def __repr__(self):
        return f"<Usage(id={self.id}, date={self.usage_date}, room_id={self.room_id}, kWh={self.kwh_used})>"
class Sensor(db.Model):
    __tablename__ = 'sensors'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    serial_number = db.Column(db.String(100), nullable=True)
    
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=False, unique=True)
    
    
    device = db.relationship('Device', back_populates='sensor')
    usages = db.relationship('DeviceUsage', back_populates='sensor', cascade="all, delete-orphan", lazy=True)

    def __repr__(self):
        return f"<Sensor {self.name}>"


class DeviceUsage(db.Model):
    __tablename__ = 'device_usages'
    
    id = db.Column(db.BigInteger, primary_key=True)
    
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=False)
    sensor_id = db.Column(db.Integer, db.ForeignKey('sensors.id'), nullable=False)
    
    voltage = db.Column(db.Float, nullable=True)
    current = db.Column(db.Float, nullable=True)
    power = db.Column(db.Float, nullable=True)
    energy_kwh = db.Column(db.Float, nullable=False)
    
    reading_time = db.Column(db.DateTime, nullable=False, default=datetime.now)
    
    
    device = db.relationship('Device', back_populates='device_usages')
    sensor = db.relationship('Sensor', back_populates='usages')

    def __repr__(self):
        return f"<DeviceUsage {self.energy_kwh}kWh at {self.reading_time}>"