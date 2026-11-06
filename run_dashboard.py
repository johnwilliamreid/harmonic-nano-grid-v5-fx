import time
from dashboard import start_dashboard_server

if __name__ == "__main__":
    start_dashboard_server(8080)
    print("Cockpit live on http://127.0.0.1:8080 — press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
