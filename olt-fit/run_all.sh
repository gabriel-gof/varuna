#!/bin/bash

# Check if the IP address is provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <OLT_IP>"
    exit 1
fi

OLT_IP=$1

# Run the Expect script to gather ONU data
./run_fit_commands.sh $OLT_IP > /Users/gabriel/Development/Gabisat/output_$OLT_IP.txt

# Run the Python script to process the output and generate JSON
python3 parse_output.py $OLT_IP