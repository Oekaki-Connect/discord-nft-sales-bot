# Multi-Collection NFT Sales Bot

A Discord bot that monitors and posts real-time NFT sales, mints, and burns for multiple collections across different blockchains.

## Features

- Real-time monitoring of NFT sales, mints, and burns across multiple collections
- Support for multiple blockchains: Ethereum, Base, Abstract, and Polygon (whichever chains Magic Eden and OpenSea support)
- Posts formatted embeds to Discord channels with detailed transaction information
- Automatic ENS name resolution for Ethereum addresses (via https://ensideas.com/)
- USD price conversion for ETH-based sales (via Coinbase spot price)
- Token image fetching and display
- Deduplication system to prevent duplicate posts
- Token ID cooldown to avoid people spamming the same token
- Configurable polling intervals per collection
- Custom burn messages with weighted randomization
- Integration with Magic Eden API (does not require API key) and OpenSea API (optional as it requires an API key)
- Can run as a systemd service on Linux with automatic restart

## Prerequisites

- Python 3.10 or higher
- Discord account with a bot application
- Discord server with appropriate channels set up
- (Optional) OpenSea API key for additional data source

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Oekaki-Connect/discord-nft-sales-bot
cd discord-nft-sales-bot
```

### 2. Install Python Dependencies

Create a virtual environment (recommended):

```bash
python3 -m venv venv
source venv/bin/activate  # On Linux/Mac
# OR
venv\Scripts\activate  # On Windows
```

Install required packages:

```bash
pip install -r requirements.txt
```

The bot requires:
- `discord.py>=2.0.0` - Discord bot framework
- `requests>=2.0.0` - HTTP requests library

### 3. Set Up Discord Bot

#### Creating the Discord Bot Application

1. Go to https://discord.com/developers/applications
2. Click "New Application" and give it a name
3. Navigate to the "Bot" section in the left sidebar
4. Click "Add Bot"
5. Under the bot's username, click "Reset Token" to generate a new token
6. Copy the token (you'll need this for the next step)

#### Bot Permissions

Your bot needs the following permissions:
- Read Messages/View Channels
- Send Messages
- Embed Links
- Attach Files
- Read Message History

In the "OAuth2" > "URL Generator" section:
1. Select the "bot" scope
2. Select the permissions listed above
3. Copy the generated URL and use it to invite the bot to your server

#### Save the Bot Token

Create a file named `discord_bot.token` in the project root:

```bash
echo "YOUR_DISCORD_BOT_TOKEN_HERE" > discord_bot.token
```

Replace `YOUR_DISCORD_BOT_TOKEN_HERE` with the token you copied earlier.

### 4. Get Discord Channel IDs

To get channel IDs for your Discord server:

1. Enable Developer Mode in Discord:
   - Open Discord User Settings
   - Go to "App Settings" > "Advanced"
   - Enable "Developer Mode"

2. Get Channel IDs:
   - Right-click on any channel in your server
   - Click "Copy Channel ID"
   - Repeat for each channel where you want sales, mints, or burns posted

### 5. (Optional) Set Up OpenSea API

The bot works with Magic Eden API by default. OpenSea integration is optional but provides an additional data source.

1. Get an OpenSea API key from https://docs.opensea.io/reference/api-keys
2. Create a file named `opensea.token` in the project root:

```bash
echo "YOUR_OPENSEA_API_KEY_HERE" > opensea.token
```

If this file doesn't exist, the bot will simply skip OpenSea checks.

## Configuration

### Collection Configuration File

Edit `collection_configs.json` to add or modify NFT collections to monitor. The file structure is:

```json
{
  "collections": [
    {
      "name": "Collection Name",
      "chain": "ethereum",
      "contract_address": "0x...",
      "transaction_link_base": "https://etherscan.io/tx/",
      "discord_sales_channel_id": 1234567890,
      ...
    }
  ]
}
```

The existing collection_configs.json shows samples of how to setup the configs.

### Required Configuration Parameters

- **name** (string): Display name for the collection
- **chain** (string): Blockchain network - must be one of: `ethereum`, `base`, `abstract`, or `polygon`
- **contract_address** (string): The NFT contract address (case-insensitive)
- **transaction_link_base** (string): Base URL for transaction explorer links
  - Ethereum: `https://etherscan.io/tx/`
  - Base: `https://basescan.org/tx/`
  - Abstract: `https://abscan.org/tx/`
  - Polygon: `https://polygonscan.com/tx/`

### Optional Configuration Parameters

- **opensea_collection_slug** (string): OpenSea collection identifier (e.g., "bored-ape-yacht-club")
  - Required only if you want to use OpenSea API for this collection
  - Find this in the OpenSea collection URL: opensea.io/collection/SLUG-HERE

- **poll_interval** (integer): Seconds between API checks (default: 300)
  - Lower values = more frequent checks = more API calls
  - Recommended: 60-360 seconds depending on collection activity

- **sales_limit** (integer): Maximum sales to fetch per API call (default: 50)

- **activity_limit** (integer): Maximum activities to fetch per API call (default: 50)

- **max_known_sales** (integer): Maximum sales IDs to track for deduplication (default: 50)

- **max_known_mints** (integer): Maximum mint IDs to track (default: 100)

- **max_known_burns** (integer): Maximum burn IDs to track (default: 100)

- **id_cooldown** (integer): Minutes to wait before reposting the same token ID (default: 60)
  - Prevents spam when a token is rapidly traded

- **discord_sales_channel_id** (integer): Discord channel ID where sales will be posted

- **discord_mint_channel_id** (integer): Discord channel ID where mints will be posted

- **discord_burn_channel_id** (integer): Discord channel ID where burns will be posted

- **zero_address** (string): Address to filter out (default: "0x0000000000000000000000000000000000000000")

- **burn_address** (string): Alternative burn address to monitor (optional)

- **json_base_uri** (string): Base URI for fetching token metadata
  - Used when token images are not available from the activity API
  - Example: "https://ipfs.io/ipfs/YOUR_IPFS_HASH" or "https://yourdomain.com/metadata"

- **burn_messages** (array): Custom burn messages with weighted randomization
  - Each message has a `weight` (0-1, should sum to 1.0) and `message` (string)
  - Use `{tokenName}` placeholder which will be replaced with the actual token name
  - Example:
    ```json
    "burn_messages": [
      { "weight": 0.5, "message": "{tokenName} has been burned!" },
      { "weight": 0.3, "message": "{tokenName} went up in flames!" },
      { "weight": 0.2, "message": "{tokenName} is no more!" }
    ]
    ```

### Example Configuration

```json
{
  "collections": [
    {
      "name": "My NFT Collection",
      "chain": "ethereum",
      "opensea_collection_slug": "my-nft-collection",
      "contract_address": "0x1234567890abcdef1234567890abcdef12345678",
      "transaction_link_base": "https://etherscan.io/tx/",
      "poll_interval": 120,
      "sales_limit": 50,
      "activity_limit": 50,
      "max_known_sales": 50,
      "max_known_mints": 100,
      "max_known_burns": 100,
      "id_cooldown": 60,
      "discord_sales_channel_id": 1234567890123456789,
      "discord_mint_channel_id": 1234567890123456790,
      "discord_burn_channel_id": 1234567890123456791,
      "json_base_uri": "https://ipfs.io/ipfs/QmYourIPFSHash",
      "burn_messages": [
        { "weight": 0.6, "message": "{tokenName} has been burned!" },
        { "weight": 0.3, "message": "{tokenName} is gone forever!" },
        { "weight": 0.1, "message": "{tokenName} has ascended!" }
      ]
    }
  ]
}
```

### ENS Configuration

ENS (Ethereum Name Service) lookup is enabled by default. To disable it:

Edit `bot.py` and change line 13:

```python
ENS_ENABLED = False  # Set to False if you do not want to do ENS lookups
```

When enabled, the bot will attempt to resolve Ethereum addresses to ENS names (e.g., "vitalik.eth") for a better user experience.

## Running the Bot

### Run Locally

Activate your virtual environment (if not already activated):

```bash
source venv/bin/activate  # On Linux/Mac
# OR
venv\Scripts\activate  # On Windows
```

Start the bot:

```bash
python bot.py
```

The bot will start and begin monitoring configured collections. Press Ctrl+C to stop.

### Run as a Linux Service (Recommended for Production)

Running the bot as a systemd service ensures it starts automatically on boot and restarts if it crashes.

#### 1. Edit Service File

Edit `sales_bot.service` and update the paths if needed:

```ini
# /etc/systemd/system/sales_bot.service
[Unit]
Description=Sales Bot Service
After=network.target

[Service]
Type=simple

User=YOUR_USERNAME
Group=YOUR_GROUP

# Update these paths to match your installation
WorkingDirectory=/path/to/multi-collection-sales-bot
ExecStart=/path/to/multi-collection-sales-bot/venv/bin/python /path/to/multi-collection-sales-bot/bot.py

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Replace:
- `YOUR_USERNAME` with your Linux username (e.g., `ubuntu`, `botmaster`)
- `YOUR_GROUP` with your user's group (typically the same as username, or `sudo`)
- `/path/to/multi-collection-sales-bot` with the actual path to your bot installation

#### 2. Install Service File

Copy the service file to systemd directory:

```bash
sudo cp sales_bot.service /etc/systemd/system/
```

#### 3. Set Permissions

Ensure the service file has correct permissions:

```bash
sudo chmod 644 /etc/systemd/system/sales_bot.service
```

#### 4. Enable and Start Service

Reload systemd to recognize the new service:

```bash
sudo systemctl daemon-reload
```

Enable the service to start on boot:

```bash
sudo systemctl enable sales_bot
```

Start the service:

```bash
sudo systemctl start sales_bot
```

#### 5. Service Management Commands

Check service status:

```bash
sudo systemctl status sales_bot
```

View logs:

```bash
sudo journalctl -u sales_bot -f
```

Restart service (after making configuration changes):

```bash
sudo systemctl restart sales_bot
```

Stop service:

```bash
sudo systemctl stop sales_bot
```

Disable service from starting on boot:

```bash
sudo systemctl disable sales_bot
```

## How the Bot Works

### Activity Monitoring

The bot polls the Magic Eden API (and optionally OpenSea API) at configurable intervals for each collection. It monitors three types of activities:

1. **TRADE (Sales)**: NFT purchases and sales
2. **MINT**: New NFT mints
3. **BURN**: NFT burns

### Deduplication System

To prevent duplicate posts, the bot maintains tracking files:
- `known_sales_{contract_address}.txt`
- `known_mints_{contract_address}.txt`
- `known_burns_{contract_address}.txt`

Each activity is stored as `{tokenId}-{txHash}`. The bot automatically prunes old entries to keep file sizes manageable.

### Token ID Cooldown

To prevent spam when a token is rapidly traded, the bot enforces a cooldown period (default 60 minutes, configurable per collection). During the cooldown, the same token ID won't be posted again.

### Discord Embeds

The bot posts rich embeds to Discord with:

**Sales:**
- Token name and image
- Sale price in native currency (ETH, etc.) and USD
- Seller address (with ENS resolution)
- Buyer address (with ENS resolution)
- Transaction link

**Mints:**
- Token name and image
- Minter address (with ENS resolution)
- Transaction link

**Burns:**
- Token name and image
- Custom burn message (weighted random)
- Previous owner address (with ENS resolution)
- Transaction link

## Troubleshooting

### Bot doesn't start

- Check that `discord_bot.token` exists and contains a valid token
- Verify Python version: `python --version` (must be 3.10+)
- Ensure all dependencies are installed: `pip install -r requirements.txt`
- Check for error messages in the console or logs

### No sales/mints are being posted

- Verify Discord channel IDs are correct in `collection_configs.json`
- Ensure the bot has permission to post in the configured channels
- Check that the contract address is correct
- Verify the collection has activity (check on Magic Eden or OpenSea)
- Review console output for API errors
- Try lowering `poll_interval` to check more frequently

### Bot posts duplicate sales

- Check that `known_sales_*.txt` files are being created and updated
- Verify the bot has write permissions in its directory
- Ensure only one instance of the bot is running
- Review `max_known_sales` setting (increase if needed)

### ENS resolution is slow

- ENS lookups can add latency; results are cached to minimize impact
- Disable ENS if speed is critical: Set `ENS_ENABLED = False` in `bot.py` line 13

### OpenSea integration not working

- Verify `opensea.token` file exists and contains a valid API key
- Check console output for OpenSea API errors
- Ensure collection has `opensea_collection_slug` configured
- Note: OpenSea API has rate limits; the bot handles this gracefully

### Service fails to start on Linux

- Check service logs: `sudo journalctl -u sales_bot -xe`
- Verify paths in `sales_bot.service` are correct
- Ensure user has permissions to execute files
- Check that virtual environment exists and has dependencies installed
- Verify `discord_bot.token` is readable by the service user

### Bot crashes or stops unexpectedly

- Check logs: `sudo journalctl -u sales_bot -n 100`
- Verify network connectivity
- Ensure sufficient disk space for tracking files
- Review system resources (RAM, CPU)
- The systemd service will automatically restart the bot after 5 seconds

## File Structure

```
multi-collection-sales-bot/
├── bot.py                      # Main bot application
├── collection_configs.json     # Collection configuration
├── requirements.txt            # Python dependencies
├── sales_bot.service          # Systemd service file (edit this)
├── discord_bot.token          # Discord bot token (create this)
├── opensea.token              # OpenSea API key (optional)
├── known_sales_*.txt          # Sales tracking (auto-generated)
├── known_mints_*.txt          # Mints tracking (auto-generated)
├── known_burns_*.txt          # Burns tracking (auto-generated)
├── venv/                      # Python virtual environment (create this)
├── .gitignore                 # Git ignore rules
├── LICENSE                    # GNU AGPL v3
└── README.md                  # This file
```

## API Rate Limits

- **Magic Eden**: No authentication required, reasonable rate limits
- **OpenSea**: Requires API key, has rate limits (handled by bot)
- **ENS**: Uses free ENS Ideas API, results cached to minimize requests
- **Coinbase**: Used for ETH-USD conversion, no auth required

To avoid rate limiting issues:
- Set appropriate `poll_interval` values (300+ seconds recommended)
- Don't run multiple instances monitoring the same collections
- Monitor console output for API errors

## Security Notes

- Keep `discord_bot.token` and `opensea.token` files secure and never commit them to version control
- The `.gitignore` file is configured to exclude these sensitive files
- Run the service with a non-root user for better security
- Regularly update dependencies to patch security vulnerabilities

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Support

For issues, questions, or contributions, please open an issue on the GitHub repository.

## Credits

Powered by Oekaki.io

You can edit this to whatever you want!