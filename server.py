import asyncio

from flask import Flask
from lane_api import APIClient

from threading import Thread

app = Flask(__name__)
api_client = APIClient()

lane_group_status = {}
lane_group_data = {}


def update_cb(lane_group_id, event_data):
    lane_group_data[lane_group_id] = event_data
    print(event_data)


def update_task(lane_group_id):
    asyncio.run(api_client.get_live_status(lane_group_id, update_cb, lane_group_status[lane_group_id]))


def start_update_task(lane_group_id):
    is_present = lane_group_id in lane_group_status

    if is_present:
        lane_group_status[lane_group_id][0] = 5
        return
    else:
        lane_group_status[lane_group_id] = [5]
    thread = Thread(target=update_task, args=(lane_group_id,))
    thread.start()


@app.route("/lane-group/<lane_group_id>")
def hello_world(lane_group_id):
    start_update_task(lane_group_id)
    if lane_group_id in lane_group_data:
        return lane_group_data[lane_group_id]
    else:
        return []

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)