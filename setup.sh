#!/usr/bin/env bash

echo "================================================="
echo "   🪨 GRUG IS AWAKENING - INITIAL SETUP 🪨   "
echo "================================================="
echo ""
echo "Welcome to Grug! Let's get your Slack app wired up."

# 1. Environment Variable Check
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
    echo "[✓] Grug see .env file. Grug no need ask for magic string."
else
    echo "Grug need shiny tokens to talk to Slack bird and Cloud Brain."
    echo ""
    read -p "Give SLACK_BOT_TOKEN (xoxb-...): " slack_bot
    read -p "Give SLACK_APP_TOKEN (xapp-...): " slack_app
    read -p "Give CLAUDE_API_KEY (Leave empty if Grug only use local Edge Rock): " claude_key

    echo "SLACK_BOT_TOKEN=$slack_bot" > .env
    echo "SLACK_APP_TOKEN=$slack_app" >> .env
    echo "CLAUDE_API_KEY=$claude_key" >> .env
    echo ""
    echo "[✓] Magic strings safe in .env!"
fi

# 2. Folder Architecture
echo ""
echo "Grug carve out brain caves..."
mkdir -p brain/daily_notes
echo "[✓] Rock caves created."

# 3. Docker Boot
echo ""
echo "Grug rub sticks together to start Docker Fire (take minute)..."
docker-compose up -d --build

echo ""
echo "================================================="
echo "        🔥 GRUG READY FOR HUNTING 🔥         "
echo "================================================="
echo "• Talk to Grug on Slack."
echo "• Watch Grug think: docker-compose logs -f grug-orchestrator"
echo "• Make Grug sleep: docker-compose down"
