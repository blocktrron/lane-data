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


@staticmethod
def get_directions_for_lane_group(lane_group_id):
    # Load lanes.json
    with open("lanes.json", "r") as lane_file:
        lanes = json.load(lane_file)

    lane_directions = {}
    for feature in lanes["features"]:
        if feature["properties"]["laneGroupId"] == lane_group_id:
            lane_directions[feature["properties"]["laneId"]] = []
            for connection in feature["properties"]["connections"]:
                for maneuver in connection["maneuvers"]:
                    if "LEFT" in maneuver:
                        lane_directions[feature["properties"]["laneId"]].append(LaneDirection.LEFT)
                    elif "RIGHT" in maneuver:
                        lane_directions[feature["properties"]["laneId"]].append(LaneDirection.RIGHT)
                    elif "STRAIGHT" in maneuver:
                        lane_directions[feature["properties"]["laneId"]].append(LaneDirection.STRAIGHT)

    # Remove duplicates
    for lane_id, directions in lane_directions.items():
        lane_directions[lane_id] = list(set(directions))

    return lane_directions


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

    def get_lane_map(self, box_id):
        response = requests.get(self.get_url("get_lane_map").replace("{spatboxId}", box_id), headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + self.token
        })

        lanes = []
        for feature in response.json()[0]["features"]:
            # We only consider the Car lanes. There seems to be more lanes though (foot-traffic / bikes?)
            if "CAR" not in feature.get("properties", {}).get("trafficTypes", []):
                continue
            if feature.get("properties", {}).get("laneType") != "VEHICLE":
                continue
            lanes.append(Lane(feature=feature))

        return lanes

    def get_live_status(self, lane_group_id):
        # Lane-Group contains the intersection-id.
        # Be careful, we are NOT requesting using a specific lane but rather a lane-group!
        lane_directions = get_directions_for_lane_group(lane_group_id)

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
            print("-- Update received --")
            lane_events = json.loads(event.data)[0]

            # Order lane_events from left to right. Sort inverted.
            # First on screen is leftmost lane.
            lane_events = sorted(lane_events, key=lambda x: x[0].split("_")[1], reverse=True)

            for lane_event in lane_events:
                lane_id = lane_event[0]
                trafficlight_state = lane_event[1]
                time_left = lane_event[2]
                timestamp = lane_event[3]
                lane_direction = lane_directions.get(lane_id, [])

                # Handle dummy values (Traffic-Light off / Grey?)
                if time_left > 200 or time_left < 0:
                    time_left = -1

                # Skip lanes without direction. Possibly they are pedestrian lanes?
                if len(lane_direction) == 0:
                    continue

                lane_direction_str = ", ".join([str(direction.value) for direction in lane_direction])
                print(f"{time_left} \t {trafficlight_state} \t {lane_direction_str} \t {lane_id}")

            # Newline
            print("")


def print_usage():
    print("Usage: python3 signal2x-client.py <command> [<lange-group-id>]")
    print("Commands:")
    print("  get-intersections")
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
    elif command == "get-lane-map":
        # Download all available intersections first
        intersections = api_client.get_intersections(api_data["bbox"]["darmstadt"])

        # Get all Lanes for the intersections
        lanes = []
        for intersection in intersections:
            lane_map = api_client.get_lane_map(intersection.id)
            lanes += lane_map

            # Wait for random time 100-950ms to not overload the server
            time.sleep(random.randint(100, 950) / 1000)

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
