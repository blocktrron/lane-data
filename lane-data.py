#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import enum
import json
import random
import requests
import sseclient
import sys
import time
import urllib3

from dataclasses import dataclass
from datetime import datetime

api_data = {
    "server": "https://spat.signal2xprod.aws.vmz.services",
    "bbox": {
        "darmstadt": "8.609031997891949,49.799331422494326,8.701769255695297,49.94381976422014",
    },
    "urls": {
        "get_token": "/auth/realms/spat/protocol/openid-connect/token",
        "get_boxes": "/spatmap/spat-ui-backend/v1/map-data/spatBoxes",
        "get_trigger_lines": "/spatmap/spat-ui-backend/v1/map-data/spatBoxes/{spatboxId}/triggerline",
        "get_lane_map": "/spatmap/spat-ui-backend/v1/map-data/spatBoxes/{spatboxId}/signalizedIntersectionMap",
        "get_live_status":
            "/spatmap/spat-ui-backend/v1/live-data/spatBoxes/{spatboxId}/laneGroups/{laneGroupId}/broadcast",
    }
}


@staticmethod
def parse_timestamp(timestamp):
    return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ[UTC]")


@dataclass
class Box:
    id: str
    timestamp: datetime
    latitude: float
    longitude: float
    feature: dict

    def __init__(self, feature):
        self.id = feature["properties"]["spatboxId"]
        self.timestamp = parse_timestamp(feature["properties"]["timestamp"])
        self.latitude = feature["geometry"]["coordinates"][1]
        self.longitude = feature["geometry"]["coordinates"][0]
        self.feature = feature


@dataclass
class TriggerLine:
    feature: dict

    def __init__(self, feature):
        self.feature = feature


@dataclass
class Lane:
    id: str
    groupId: str
    feature: dict

    def __init__(self, feature):
        self.id = feature["properties"]["laneId"]
        self.groupId = feature["properties"]["laneGroupId"]
        self.feature = feature


class LaneDirection(enum.Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    STRAIGHT = "STRAIGHT"


class LaneType(enum.Enum):
    VEHICLE = "VEHICLE"
    CROSSWALK = "CROSSWALK"
    BIKE_LANE = "BIKE_LANE"
    TRACKED_VEHICLE = "TRACKED_VEHICLE"


@dataclass
class LaneProperties:
    id: str
    directions: list[LaneDirection]
    lane_type: LaneType

@staticmethod
def get_lane_properties(lane_group_id):
    # Load lanes.json
    with open("lanes.json", "r") as lane_file:
        lanes = json.load(lane_file)

    lane_properties = {}
    for feature in lanes["features"]:
        if feature["properties"]["laneGroupId"] != lane_group_id:
            continue

        lane_id = feature["properties"]["laneId"]
        lane_type = feature["properties"]["laneType"]
        lane_directions = []

        # Get possible directions for lane
        for connection in feature["properties"]["connections"]:
            for maneuver in connection["maneuvers"]:
                if "LEFT" in maneuver:
                    lane_directions.append(LaneDirection.LEFT)
                elif "RIGHT" in maneuver:
                    lane_directions.append(LaneDirection.RIGHT)
                elif "STRAIGHT" in maneuver:
                    lane_directions.append(LaneDirection.STRAIGHT)

        # Remove duplicates
        lane_directions = list(set(lane_directions))

        lane_properties[lane_id] = LaneProperties(
            id=lane_id,
            directions=lane_directions,
            lane_type=LaneType(lane_type)
        )

    return lane_properties


class APIClient:
    def __init__(self):
        self.token = self.get_token()

    @staticmethod
    def get_url(url_name):
        return api_data["server"] + api_data["urls"][url_name]

    def get_token(self):
        # Make POST request to server
        response = requests.post(self.get_url("get_token"), data={
            "grant_type": "client_credentials",
        }, headers={
            "Accept": "application/json",
            "Authorization": "Basic c3BhdC1hcHA6NmNlOTE2ZjktMWI3MC00YmZiLTg3NDItODJhMWNiM2E2NmYz"
        })

        # Parse response
        if response.status_code == 200:
            return response.json()["access_token"]
        else:
            return None

    def get_intersections(self, bbox):
        # Intersections are called boxes because they contain the trigger area for the app
        # We are only interested in the intersection locations though

        response = requests.get(self.get_url("get_boxes"), params={
            "bbox": bbox
        }, headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + self.token
        })

        intersections = []
        for intersection in response.json():
            for intersection_feature in intersection["features"]:
                # Only consider intersection locations, not trigger-boxes
                if intersection_feature["geometry"]["type"] != "Point":
                    continue
                intersections.append(Box(feature=intersection_feature))

        return intersections

    def get_trigger_lines(self, box_id):
        response = requests.get(self.get_url("get_trigger_lines").replace("{spatboxId}", box_id), headers={
            "Accept": "application/json", "Authorization": "Bearer " + self.token
        })

        for feature in response.json():
            yield TriggerLine(feature=feature)

    def get_lane_map(self, box_id):
        response = requests.get(self.get_url("get_lane_map").replace("{spatboxId}", box_id), headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + self.token
        })

        lanes = []
        for feature in response.json()[0]["features"]:
            if "trafficTypes" not in feature.get("properties", {}):
                continue
            lanes.append(Lane(feature=feature))

        return lanes

    def get_live_status(self, lane_group_id):
        # Lane-Group contains the intersection-id.
        # Be careful, we are NOT requesting using a specific lane but rather a lane-group!
        all_lane_properties = get_lane_properties(lane_group_id)

        box_id = lane_group_id.split("_")[0]
        url = (self.get_url("get_live_status")
               .replace("{spatboxId}", box_id)
               .replace("{laneGroupId}", lane_group_id))
        headers = {
            "Authorization": "Bearer " + self.token,
        }

        http = urllib3.PoolManager()
        response = http.request('GET', url, preload_content=False, headers=headers)

        client = sseclient.SSEClient(response)
        for event in client.events():
            if event.event != "SignalizedLaneGroupState":
                continue
            # Clear terminal
            print(chr(27) + "[2J")

            # Print current Date and time in german format
            print("=== " + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + " ===")
            lane_events = json.loads(event.data)[0]

            # Order lane_events from left to right. Sort inverted.
            # First on screen is leftmost lane.
            lane_events = sorted(lane_events, key=lambda x: x[0].split("_")[1], reverse=True)

            # Group lane_events by type in dict
            lane_event_by_lane_type = {}
            for lane_event in lane_events:
                lane_id = lane_event[0]
                lane_type = all_lane_properties.get(lane_id, None)
                if lane_type is None:
                    continue
                lane_event_by_lane_type.setdefault(lane_type.lane_type, []).append(lane_event)

            for lane_type in lane_event_by_lane_type:
                print(f"--- {lane_type.name} ---")
                for lane_event in lane_event_by_lane_type[lane_type]:
                    lane_id = lane_event[0]
                    trafficlight_state = lane_event[1]
                    time_left = lane_event[2]
                    timestamp = lane_event[3]
                    lane_properties = all_lane_properties.get(lane_id, None)
                    lane_direction = lane_properties.directions

                    # Handle dummy values (Traffic-Light off / Grey?)
                    if time_left > 200 or time_left < 0:
                        time_left = -1

                    if lane_properties.lane_type == LaneType.CROSSWALK:
                        lane_direction_str = "N/A"
                    elif lane_properties.lane_type in [LaneType.VEHICLE, LaneType.TRACKED_VEHICLE, LaneType.BIKE_LANE]:
                        lane_direction_str = ", ".join([str(direction.value) for direction in lane_direction])
                    print(f"{time_left} \t {lane_properties.lane_type.name} \t {trafficlight_state} \t {lane_direction_str} \t {lane_id}")

            # Newline
            print("")


def print_usage():
    print("Usage: python3 signal2x-client.py <command>")
    print("Commands:")
    print("  get-intersections")
    print("  get-trigger-lines <intersection-id>")
    print("  get-lane-map")
    print("  live-status <lane-group-id>")


if __name__ == "__main__":
    # Parse command line arguments
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    api_client = APIClient()
    command = sys.argv[1]
    if command == "get-intersections":
        boxes = api_client.get_intersections(api_data["bbox"]["darmstadt"])

        # Write all lanes to file
        geojson_output = {"type": "FeatureCollection", "features": [box.feature for box in boxes]}
        with open("intersections.json", "w") as f:
            json.dump(geojson_output, f, indent=4)
    elif command == "get-trigger-lines":
        if len(sys.argv) < 3:
            print_usage()
            sys.exit(1)
        trigger_lines = api_client.get_trigger_lines(sys.argv[2])

        # Write all trigger-lines to file
        geojson_output = {"type": "FeatureCollection", "features": [tl.feature for tl in trigger_lines]}
        with open("triggers.json", "w") as f:
            json.dump(geojson_output, f, indent=4)
    elif command == "get-lane-map":
        # Download all available intersections first
        intersections = api_client.get_intersections(api_data["bbox"]["darmstadt"])

        # Get all Lanes for the intersections
        lanes = []
        total_intersections = len(intersections)
        
        for index, intersection in enumerate(intersections):
            # Get the lane map for the current intersection
            lane_map = api_client.get_lane_map(intersection.id)
            lanes += lane_map
        
            # Calculate the progress in percentage
            progress_percentage = (index + 1) / total_intersections * 100
        
            # Arrow from left to right with 50 steps
            arrow_progress = int(progress_percentage / 2)
            arrow = "=" * arrow_progress + ">" + "." * (50 - arrow_progress)
        
            # Wait for a random time between 100 and 950 ms to avoid overloading the server
            time.sleep(random.randint(100, 400) / 1000)
        
            # Print the progress as an arrow and clear the previous progress
            print(f"\rProgress: [{arrow}] {progress_percentage:.2f}%", end='', flush=True)


        # Write all lanes to file
        geojson_output = {"type": "FeatureCollection", "features": [lane.feature for lane in lanes]}
        with open("lanes.json", "w") as f:
            json.dump(geojson_output, f, indent=4)
    elif command == "live-status":
        if len(sys.argv) < 3:
            print_usage()
            sys.exit(1)
        lane_group_id = sys.argv[2]
        api_client.get_live_status(lane_group_id)
