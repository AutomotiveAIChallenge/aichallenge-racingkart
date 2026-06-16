#!/bin/bash

EXEC_BASH=(bash --rcfile /etc/skel/.bashrc -i)

# Display title
echo "======================================"
echo "  Docker Container Exec Tool"
echo "======================================"
echo ""

# Check running containers
RUNNING_CONTAINERS=$(docker ps -q)
if [ -z "$RUNNING_CONTAINERS" ]; then
    echo "No running Docker containers found."
    exit 1
fi

# Display running container list (numbered)
echo "Running containers:"
echo "--------------------------------------"
docker ps --format "{{.ID}}\t{{.Names}}" | nl -w2 -s') '
echo "--------------------------------------"
echo ""

# Count number of running containers
CONTAINER_COUNT=$(echo "$RUNNING_CONTAINERS" | wc -l)

# If only one container is running, select it automatically
if [ "$CONTAINER_COUNT" -eq 1 ]; then
    SELECTED_CONTAINER=$(docker ps -q)
    CONTAINER_NAME=$(docker ps --format "{{.Names}}")
    echo "Only one container running: $CONTAINER_NAME"
    echo "Automatically connecting to this container..."
    docker exec -it "$SELECTED_CONTAINER" "${EXEC_BASH[@]}"
    exit 0
fi

# Ask user to select a container by number
echo "Enter the number of the container you want to execute a command in:"
read -r CONTAINER_NUM

# Get the container ID from the selected number
SELECTED_CONTAINER=$(docker ps --format "{{.ID}}" | sed -n "${CONTAINER_NUM}p")

if [ -z "$SELECTED_CONTAINER" ]; then
    echo "Invalid selection. Exiting."
    exit 1
fi
# Get the container name
CONTAINER_NAME=$(docker ps --format "{{.Names}}" | sed -n "${CONTAINER_NUM}p")
# Display which container we're connecting to
echo "Connecting to container: $CONTAINER_NAME (ID: $SELECTED_CONTAINER)"

# Execute bash in the selected container
docker exec -it "$SELECTED_CONTAINER" "${EXEC_BASH[@]}"
