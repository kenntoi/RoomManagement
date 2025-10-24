from werkzeug.security import generate_password_hash, check_password_hash
from . import db

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
        print("Set password hash:", self.password)
    def check_password(self, secret):
        """Checks if the provided password matches the stored hashed password."""
        return check_password_hash(self.password, secret)
    
class Schedule(db.Model):
    __tablename__ = 'schedules'

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.String(10), nullable=False)  # e.g., 'Monday', 'Tuesday'
    start_time = db.Column(db.DateTime, nullable=False)  # Changed to DateTime
    end_time = db.Column(db.DateTime, nullable=False)  # Changed to DateTime
    subject = db.Column(db.String(100), nullable=False)
    teacher = db.Column(db.String(100), nullable=False)
    

    def __repr__(self):
        return f"<Schedule {self.subject} on {self.day} from {self.start_time} to {self.end_time}>"

class Room(db.Model):
    __tablename__ = 'rooms'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    
    # Relationship to access devices in a room
    devices = db.relationship('Device', backref='room', cascade="all, delete-orphan")
    # Relationship to access usages in a room
    usages = db.relationship('Usage', back_populates='room', cascade="all, delete-orphan")


    def __repr__(self):
        return f"<Room(id={self.id}, name='{self.name}')>"


class Device(db.Model):
    __tablename__ = 'devices'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    state = db.Column(db.Integer, nullable=False, default=0)  # 0 = off, 1 = on
    
    # Foreign key to link the device to a room
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)

    def __repr__(self):
        return f"<Device(id={self.id}, name='{self.name}', state={self.state}, room_id={self.room_id})>"

class Usage(db.Model):
    __tablename__ = 'usages'
    
    id = db.Column(db.Integer, primary_key=True)
    usage_date = db.Column(db.Date, nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)  # Foreign key to Room
    kwh_used = db.Column(db.Float, nullable=False)  # Store the kWh used for that day
    
    room = db.relationship("Room", back_populates="usages")