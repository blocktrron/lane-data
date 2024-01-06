#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import random
import sys
import time

from lane_api import APIClient, api_data



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
        intersections = api_client.get_intersections(api_data["bbox"]["darmstadt"])

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
        with open("lanes.json", "w") as f:
            json.dump(geojson_output, f, indent=4)
    elif command == "live-status":
        if len(sys.argv) < 3:
            print_usage()
            sys.exit(1)
        lane_group_id = sys.argv[2]
        api_client.get_live_status(lane_group_id)
