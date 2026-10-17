#!/bin/bash
# Wait for daemon to be ready, then start the conversation app

MAX_ATTEMPTS=30
ATTEMPT=0

echo "Waiting for reachy-mini-daemon to be ready..."

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    # Check if daemon is responding
    if curl -s -X POST http://localhost:8000/health-check > /dev/null 2>&1; then
        echo "Daemon is ready..."
        sleep 2  # Give it a moment to fully initialize

        # Set volume to 100%
        echo "Setting volume to 100%..."
        curl -s -X POST http://localhost:8000/api/volume/set \
            -H "Content-Type: application/json" \
            -d '{"volume": 100}' > /dev/null 2>&1

        # Start the conversation app
        echo "Starting conversation app..."
        curl -s -X POST http://localhost:8000/api/apps/start-app/reachy_mini_conversation_app

        if [ $? -eq 0 ]; then
            echo "Conversation app started successfully"
            exit 0
        else
            echo "Failed to start conversation app"
            exit 1
        fi
    fi

    ATTEMPT=$((ATTEMPT + 1))
    echo "Attempt $ATTEMPT/$MAX_ATTEMPTS - daemon not ready yet..."
    sleep 2
done

echo "Timeout waiting for daemon"
exit 1
