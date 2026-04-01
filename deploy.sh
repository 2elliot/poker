#!/bin/bash
# Automated deployment script for Poker Tournament Server
# Run this on your fresh Linux server

set -e  # Exit on error

echo "=================================="
echo "Poker Tournament - Server Setup"
echo "=================================="
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Don't run this as root. Run as regular user with sudo access."
    exit 1
fi

# Update system
echo "📦 Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Install dependencies
echo "📥 Installing required packages..."
sudo apt install -y python3 python3-pip python3-venv nginx git supervisor certbot python3-certbot-nginx

# Create project directory
PROJECT_DIR="$HOME/poker-tournament"
if [ -d "$PROJECT_DIR" ]; then
    echo "⚠️  Directory $PROJECT_DIR already exists"
    read -p "Remove and reinstall? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$PROJECT_DIR"
    else
        echo "Exiting..."
        exit 1
    fi
fi

# Clone or create directory
echo "📁 Setting up project directory..."
if [ ! -z "$1" ]; then
    # Clone from Git repo if URL provided
    git clone "$1" "$PROJECT_DIR"
else
    mkdir -p "$PROJECT_DIR"
    echo "⚠️  No Git repo provided. Created empty directory."
    echo "Upload your code to: $PROJECT_DIR"
fi

cd "$PROJECT_DIR"

# Create virtual environment
echo "🐍 Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "📚 Installing Python packages..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    pip install flask flask-cors flask-login cryptography gunicorn
fi

# Create .env file
echo ""
echo "🔐 Setting up environment variables..."
if [ ! -f ".env" ]; then
    echo "Generating secure credentials..."
    MASTER_PASSWORD=$(openssl rand -base64 32)
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    
    cat > .env << EOF
MASTER_PASSWORD=$MASTER_PASSWORD
SECRET_KEY=$SECRET_KEY
FLASK_ENV=production
EOF
    
    echo ""
    echo "=================================="
    echo "⚠️  SAVE THESE CREDENTIALS! ⚠️"
    echo "=================================="
    echo "Master Password: $MASTER_PASSWORD"
    echo "=================================="
    echo ""
    echo "Press Enter to continue..."
    read
else
    echo "✓ .env file already exists"
fi

# Find the directory containing app.py (handles cloned subdirectories)
APP_DIR="$PROJECT_DIR"
if [ ! -f "$PROJECT_DIR/app.py" ]; then
    # Check if app.py is in a subdirectory (e.g., after git clone creates repo-named folder)
    APP_PY_PATH=$(find "$PROJECT_DIR" -maxdepth 2 -name "app.py" -not -path "*/venv/*" | head -1)
    if [ -n "$APP_PY_PATH" ]; then
        APP_DIR=$(dirname "$APP_PY_PATH")
        echo "Found app.py in: $APP_DIR"
    else
        echo "ERROR: app.py not found in $PROJECT_DIR"
        exit 1
    fi
fi

# Create systemd service
echo "Creating systemd service..."
sudo tee /etc/systemd/system/poker-tournament.service > /dev/null << EOF
[Unit]
Description=Poker Tournament Server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PROJECT_DIR/venv/bin/gunicorn --chdir $APP_DIR -w 4 -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
echo "🚀 Starting service..."
sudo systemctl daemon-reload
sudo systemctl enable poker-tournament
sudo systemctl start poker-tournament

# Check service status
if sudo systemctl is-active --quiet poker-tournament; then
    echo "✓ Service started successfully"
else
    echo "✗ Service failed to start. Check logs with:"
    echo "  sudo journalctl -u poker-tournament -n 50"
    exit 1
fi

# Configure Nginx
echo "🌐 Configuring Nginx..."

# Get server IP
SERVER_IP=$(curl -s ifconfig.me)

sudo tee /etc/nginx/sites-available/poker-tournament > /dev/null << EOF
server {
    listen 80;
    server_name $SERVER_IP;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # For SSE (Server-Sent Events)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
    }

    client_max_body_size 1M;
}
EOF

# Enable site
sudo ln -sf /etc/nginx/sites-available/poker-tournament /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx

# Configure firewall
echo "🔥 Configuring firewall..."
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP
sudo ufw allow 443/tcp  # HTTPS
echo "y" | sudo ufw enable

# Create backup script
echo "💾 Setting up backups..."
cat > "$PROJECT_DIR/backup.sh" << 'EOF'
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$HOME/backups"
APP_DIR="$HOME/poker-tournament"

mkdir -p "$BACKUP_DIR"

tar -czf "$BACKUP_DIR/backup_$DATE.tar.gz" \
    "$APP_DIR/admin_auth.json" \
    "$APP_DIR/bot_reviews" \
    "$APP_DIR/encrypted_bots" \
    "$APP_DIR/logs" \
    "$APP_DIR/.env" 2>/dev/null

# Keep only last 30 backups
ls -t "$BACKUP_DIR"/*.tar.gz 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null

echo "Backup completed: backup_$DATE.tar.gz"
EOF

chmod +x "$PROJECT_DIR/backup.sh"

# Add to crontab (daily at 2 AM)
(crontab -l 2>/dev/null; echo "0 2 * * * $PROJECT_DIR/backup.sh") | crontab -

echo ""
echo "=================================="
echo "✅ DEPLOYMENT COMPLETE!"
echo "=================================="
echo ""
echo "🌐 Your server is live at:"
echo "   http://$SERVER_IP"
echo ""
echo "📋 Next steps:"
echo "   1. Visit http://$SERVER_IP to test"
echo "   2. Setup domain (optional):"
echo "      - Point your domain to: $SERVER_IP"
echo "      - Run: sudo certbot --nginx -d yourdomain.com"
echo ""
echo "🔧 Useful commands:"
echo "   Status:  sudo systemctl status poker-tournament"
echo "   Logs:    sudo journalctl -u poker-tournament -f"
echo "   Restart: sudo systemctl restart poker-tournament"
echo ""
echo "💾 Backups run daily at 2 AM"
echo "   Location: $HOME/backups/"
echo ""
echo "=================================="