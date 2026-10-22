#!/usr/bin/env bash
# ==============================================================================
#  SUPERBAN OS // GITHUB ONE-CLICK PUBLISHER
# ==============================================================================
set -e

cd "$(dirname "$0")"

echo "=================================================================="
echo "  🛸 SUPERBAN OS // GITHUB REPOSITORY PUBLISHER"
echo "=================================================================="

# Check if git remote origin already exists
CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || true)

if [ -n "$CURRENT_REMOTE" ]; then
    echo "Current remote origin: $CURRENT_REMOTE"
    read -p "Do you want to push to this remote? (y/n): " CONFIRM
    if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
        git push -u origin main
        echo "✅ Successfully pushed to $CURRENT_REMOTE!"
        exit 0
    fi
fi

echo ""
echo "Steps to publish to GitHub:"
echo "1. Go to https://github.com/new and create a new repository (do NOT initialize with README)."
echo "2. Copy the repository URL (e.g., https://github.com/YOUR_USERNAME/superban-os.git)."
echo ""

read -p "Enter your GitHub repository URL: " REPO_URL

if [ -z "$REPO_URL" ]; then
    echo "❌ Error: Repository URL cannot be empty."
    exit 1
fi

# Set remote origin
if [ -n "$CURRENT_REMOTE" ]; then
    git remote set-url origin "$REPO_URL"
else
    git remote add origin "$REPO_URL"
fi

echo ""
echo "🚀 Pushing branch 'main' to $REPO_URL..."
git push -u origin main

echo ""
echo "=================================================================="
echo "🎉 SUCCESS: SuperBan OS is now published to GitHub!"
echo "Repository: $REPO_URL"
echo "=================================================================="
