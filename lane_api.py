import asyncio
import enum
import json
import requests
import sseclient
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
    with open("static/lanes.json", "r") as lane_file:
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

    def get_intersections(self, city):
        # Intersections are called boxes because they contain the trigger area for the app
        # We are only interested in the intersection locations though

        response = requests.get(self.get_url("get_boxes"), params={
            "bbox": api_data["bbox"][city]
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

    async def get_live_status(self, lane_group_id, cb, counter: list[int] = None):
        # Lane-Group contains the intersection-id.
        # Be careful, we are NOT requesting using a specific lane but rather a lane-group!

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

            if counter is not None:
                if counter[0] == 0:
                    return
                counter[0] -= 1

            cb(lane_group_id, json.loads(event.data)[0])

