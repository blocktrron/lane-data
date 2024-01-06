#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import json
import random
import sys
import time
from datetime import datetime

from lane_api import APIClient, api_data, get_lane_properties, LaneType


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
        boxes = api_client.get_intersections("darmstadt")

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
        intersections = api_client.get_intersections("darmstadt")

        # Get all Lanes for the intersections
        lanes = []
        total_intersections = len(intersections)
        
        for index, intersection in enumerate(intersections):
            # Get the lane map for the current intersection
            lane_map = api_client.get_lane_map(intersection.id)
            lanes += lane_map

            # Print progress
            print(f"\rProgress: {index + 1}/{total_intersections}", end='', flush=True)

            # Wait for a random time between 100 and 950 ms to avoid overloading the server
            time.sleep(random.randint(100, 950) / 1000)

        # Write all lanes to file
        geojson_output = {"type": "FeatureCollection", "features": [lane.feature for lane in lanes]}
        with open("static/lanes.json", "w") as f:
            json.dump(geojson_output, f, indent=4)
    elif command == "live-status":
        if len(sys.argv) < 3:
            print_usage()
            sys.exit(1)
        lane_group_id = sys.argv[2]

        def console_cb(lane_group_id, lane_events):
            all_lane_properties = get_lane_properties(lane_group_id)

            # Clear terminal
            print(chr(27) + "[2J")

            # Print current Date and time in german format
            print("=== " + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + " ===")

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

        asyncio.run(api_client.get_live_status(lane_group_id, console_cb))
