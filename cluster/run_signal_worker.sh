#!/bin/bash
# Launch the signal-handling Ray worker in a tmux session

# Kill any existing session
tmux kill-session -t ray-signal 2>/dev/null || true

# Create new tmux session
tmux new-session -d -s ray-signal

# Run the signal-handling worker script
tmux send-keys -t ray-signal "cd $HOME && ./ray-cluster/signal_handled_worker.sh" C-m

echo "Started signal-handling Ray worker in tmux session 'ray-signal'"
echo "View with: tmux attach -t ray-signal"
echo "This version handles SIGTERM signals properly and should be more stable" 