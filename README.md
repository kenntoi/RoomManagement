# RAMMS — Room Automated Management and Monitoring System

A web-based system for monitoring and controlling classroom devices, managing class schedules, and tracking electricity usage in real time. Built with Flask, MySQL, and Socket.IO, with ESP32 sensor integration for live energy readings.

---

## Features

- **Device Control** — Toggle individual devices or all devices in a room on/off from the dashboard. Changes broadcast instantly to all connected clients via WebSocket.
- **Schedule Automation** — Define class schedules per room and day. A background scheduler automatically turns devices on/off based on the active schedule every minute.
- **Energy Monitoring** — Tracks per-device electricity usage (voltage, current, power, kWh) logged by physical sensors. Statistics page shows daily, weekly, monthly, and hourly breakdowns.
- **ARIMA Forecasting** — Client-side ARIMA(1,1,1) time series model predicts future energy usage with 95% confidence intervals, visualized with Chart.js.
- **Role-Based Access** — Three roles: `admin`, `manager`, and `user`. Only admins can add/delete rooms, devices, users, and schedules.
- **Real-Time Updates** — Socket.IO pushes device state changes and usage data updates to all connected browsers without page refresh.
- **ESP32 Integration** — PHP API endpoint (`devices.php?esp=true`) serves device on/off states keyed by sensor serial number for ESP32 polling.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask, Flask-SocketIO, Flask-SQLAlchemy |
| Database | MySQL |
| Scheduling | APScheduler (BackgroundScheduler) |
| Frontend | Jinja2 templates, Bootstrap 5, Chart.js |
| Real-time | Socket.IO (threading async mode) |
| PHP APIs | Plain PHP — device state serving and test data injection |
| Hardware | ESP32 + energy sensors (serial number based) |
| Timezone | Asia/Manila (pytz) |

---

## Project Structure

```
RAMMS/
├── main.py                  # App entry point — runs socketio.run()
├── requirements.txt
└── website/
    ├── __init__.py          # App factory, scheduler setup, WebSocket broadcast
    ├── auth.py              # All routes: login, devices, schedules, stats, API
    ├── models.py            # SQLAlchemy models
    ├── views.py             # Minimal helper routes (login redirect, test DB)
    ├── create_fake_data.py  # Legacy fake data utility (Usage model)
    ├── static/
    │   ├── css/
    │   └── js/
    └── templates/
        ├── base.html        # Sidebar layout shell
        ├── login.html
        ├── signup.html
        ├── home.html        # Dashboard with summary cards
        ├── control_devices.html  # Device toggle UI with real-time updates
        ├── schedule.html    # Class schedule list + add modal
        ├── statistics.html  # Chart.js usage graphs + ARIMA predictions
        └── manage.html      # Admin user management

PHP/
├── api.php                  # Test data injection into device_usages
├── devices.php              # Device/sensor state API for ESP32 and frontend
└── test_data.html           # Browser tool for generating test usage data
```

---

## Database Models

| Model | Table | Description |
|---|---|---|
| `User` | `user` | Accounts with roles: admin, manager, user |
| `Room` | `rooms` | Physical rooms containing devices |
| `Device` | `devices` | Controllable devices (state 0/1) assigned to a room |
| `Sensor` | `sensors` | One-to-one with Device; holds serial number for ESP32 matching |
| `DeviceUsage` | `device_usages` | Per-reading log: voltage, current, power, energy_kwh, timestamp |
| `Schedule` | `schedules` | Class schedule with day, time range, subject, teacher, room |
| `Usage` | `usages` | Legacy room-level usage table (retained for compatibility) |

---

## Setup

### Prerequisites
- Python 3.10+
- MySQL running locally
- PHP server (XAMPP or similar) for the PHP API files

### Installation

```bash
# Clone the repo
git clone https://github.com/your-username/RAMMS.git
cd RAMMS

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### Database

1. Create a MySQL database named `flask_users`
2. Update the connection string in `website/__init__.py` if needed:
   ```python
   app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://root:@localhost/flask_users'
   ```
3. Tables are created automatically on first run via `db.create_all()`

### Populate Sensors for Existing Devices

If you have existing devices without sensors, run this SQL in phpMyAdmin:

```sql
INSERT INTO sensors (name, serial_number, device_id)
SELECT CONCAT(name, ' Sensor'), CONCAT('SN-', LPAD(id, 4, '0')), id
FROM devices
WHERE id NOT IN (SELECT device_id FROM sensors);
```

### Run

```bash
python main.py
```

App runs at `http://0.0.0.0:5000` by default.

---

## PHP API Endpoints

Place `api.php` and `devices.php` in your PHP server's web directory and update the URL in `test_data.html`.

### `devices.php`
| Parameter | Description |
|---|---|
| `?device_id=X` | Get single device with sensor info |
| `?room_id=X` | Get all devices in a room |
| `?grouped=true` | All devices grouped by room |
| `?esp=true` | Returns `{ "SN-0001": "ON", ... }` for ESP32 polling |

### `api.php` (Test Data Injection)
| Action | Description |
|---|---|
| `action=list_devices` | List all devices and their sensors |
| `action=generate` | Insert historical usage data (params: `device_id` or `room_id`, `days`, `records_per_day`) |
| `action=realtime` | Add a single real-time data point |
| `action=stats` | Show usage statistics summary |
| `action=clear&confirm=yes` | Delete all usage records for target |

---

## Environment Notes

- **Timezone**: All scheduling uses `Asia/Manila`. Change the pytz timezone in `__init__.py` if deploying elsewhere.
- **Secret Key**: Replace `'your-secret-key-here-change-in-production'` in `__init__.py` before deploying.
- **Device Limit**: Max 9 devices per room (enforced in the UI).
- **Scheduler**: Runs every 60 seconds to check active schedules and toggle devices automatically.

---

## User Roles

| Role | Permissions |
|---|---|
| `admin` | Full access — add/delete rooms, devices, users, schedules |
| `manager` | View and toggle devices, view schedules |
| `user` | View and toggle devices, view schedules |

---

## License

For academic/internal use.
