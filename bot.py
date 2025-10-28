import os
import time
import json
import requests
import asyncio
import discord
from discord.ext import tasks
import random

#################################
# OPTIONAL ENS LOOKUP
#################################
ENS_ENABLED = True  # Set to False if you do not want to do ENS lookups
ens_cache = {}  # address -> ens name (string) or None

def ensideas_lookup_sync(address: str) -> str | None:
    """
    Does an HTTP GET request to https://api.ensideas.com/ens/resolve/<address>.
    If 'name' is not null, return it, else return None.
    """
    url = f"https://api.ensideas.com/ens/resolve/{address}"
    print(f"[DEBUG] ensideas_lookup_sync() - Fetching ENS from {url}")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # "name" is the field if ENS is set
        name = data.get("name")
        return name  # Could be None or a string like "capsulemachine.eth"
    except Exception as e:
        print(f"[DEBUG] ENS lookup failed for {address}: {e}")
        return None

async def get_ens_or_short(address: str, chain: str) -> str:
    """
    If ENS is enabled, use the ensideas_lookup_sync to see
    if there's an ENS name. Otherwise, return a shortened address.
    """
    # Always keep addresses consistent (lowercase)
    address = address.lower()

    # If ENS is disabled, just return short address
    if not ENS_ENABLED:
        return shorten_address(address)

    # If cached, return cached result if present
    if address in ens_cache:
        cached_name = ens_cache[address]
        return cached_name if cached_name else shorten_address(address)

    # Not cached, so fetch from ensideas in a thread
    result_name = await asyncio.to_thread(ensideas_lookup_sync, address)
    if result_name:
        ens_cache[address] = result_name
        return result_name
    else:
        ens_cache[address] = None
        return shorten_address(address)


#################################
# Load Secrets
#################################
def load_file_secret(path):
    """Load a single secret (for Discord bot token)."""
    print(f"[DEBUG] Loading single file secret from {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find file: {path}")
    with open(path, "r") as f:
        secret = f.read().strip()
    print("[DEBUG] Successfully loaded single secret.")
    return secret

DISCORD_BOT_TOKEN = load_file_secret("discord_bot.token")

# OpenSea support is optional - only enable if API key file exists
OPENSEA_ENABLED = False
OPENSEA_API_KEY = None
if os.path.exists("opensea.token"):
    try:
        OPENSEA_API_KEY = load_file_secret("opensea.token")
        OPENSEA_ENABLED = True
        print("[DEBUG] OpenSea API key loaded successfully. OpenSea support enabled.")
    except Exception as e:
        print(f"[DEBUG] Error loading OpenSea API key: {e}. OpenSea support disabled.")
else:
    print("[DEBUG] opensea.token file not found. OpenSea support disabled.")

#################################
# Load Collection Configs
#################################
def load_collection_configs(path):
    print(f"[DEBUG] Loading collection configs from {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find config file: {path}")
    with open(path, "r") as f:
        data = json.load(f)
    print("[DEBUG] Collection configs loaded successfully.")
    return data

collections_data = load_collection_configs("collection_configs.json")
COLLECTIONS = collections_data["collections"]
print(f"[DEBUG] Found {len(COLLECTIONS)} collections in the config file.")

#################################
# Known IDs & Timestamps
#################################
known_sales = {}
known_mints = {}
known_burns = {}

# Track token ID cooldowns per collection
token_id_cooldowns = {}  # contract -> {token_id -> timestamp}

last_check_sales_timestamp = {}
last_check_activity_timestamp = {}
last_check_opensea_timestamp = {}

def get_sales_file(contract_address):
    return f"known_sales_{contract_address}.txt"

def get_mints_file(contract_address):
    return f"known_mints_{contract_address}.txt"

def get_burns_file(contract_address):
    return f"known_burns_{contract_address}.txt"

def is_valid_id_format(line):
    """Check if ID is in expected format: {tokenId}-{txHash}"""
    if "-" not in line:
        return False
    parts = line.split("-", 1)  # Split on first hyphen only
    if len(parts) != 2:
        return False
    token_id, tx_hash = parts
    # Token ID should be numeric, tx hash should start with 0x
    return token_id.isdigit() and tx_hash.startswith("0x")

def load_ids(filename):
    print(f"[DEBUG] Loading IDs from {filename}")
    if not os.path.exists(filename):
        print(f"[DEBUG] File does not exist: {filename}, returning empty list.")
        return []
    with open(filename, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    # Keep only entries in the expected format: tokenId-0xHash
    old_count = len(lines)
    cleaned_lines = [line for line in lines if is_valid_id_format(line)]

    if len(cleaned_lines) < old_count:
        pruned_count = old_count - len(cleaned_lines)
        print(f"[DEBUG] Pruned {pruned_count} invalid format entries from {filename}")
        # Save the cleaned list back to file
        save_ids(cleaned_lines, filename)

    print(f"[DEBUG] Loaded {len(cleaned_lines)} IDs from {filename}")
    return cleaned_lines

def save_ids(ids_list, filename):
    print(f"[DEBUG] Saving {len(ids_list)} IDs to {filename}")
    with open(filename, "w") as f:
        for _id in ids_list:
            f.write(f"{_id}\n")

print("[DEBUG] Initializing known IDs and timestamps for each collection...")
for coll in COLLECTIONS:
    contract = coll["contract_address"].lower()
    print(f"[DEBUG] Initializing for contract {contract} ({coll.get('name', '')})")
    known_sales[contract] = load_ids(get_sales_file(contract))
    known_mints[contract] = load_ids(get_mints_file(contract))
    known_burns[contract] = load_ids(get_burns_file(contract))
    token_id_cooldowns[contract] = {}  # Initialize empty cooldown dict for this collection

    now_ts = int(time.time())
    last_check_sales_timestamp[contract] = now_ts
    last_check_activity_timestamp[contract] = now_ts
    last_check_opensea_timestamp[contract] = now_ts

print("[DEBUG] Initialization complete.")

#################################
# Discord Bot
#################################
intents = discord.Intents.default()
intents.message_content = True

bot = discord.Client(intents=intents)

#################################
# Task Loop
#################################
@tasks.loop(seconds=60)
async def check_all_collections():
    current_ts = int(time.time())
    print("[DEBUG] check_all_collections() - Start checking each collection...")

    for coll in COLLECTIONS:
        coll_name = coll.get("name", "Unknown")
        contract = coll["contract_address"].lower()

        # default poll_interval to 300 (5 mins) if not specified
        poll_interval = coll.get("poll_interval", 300)

        # Check Activities (sales, mints, burns) if poll_interval has passed
        if current_ts - last_check_activity_timestamp[contract] >= poll_interval:
            print(f"[DEBUG] -> Checking Activities for: {coll_name}")
            await check_activities_for_collection(coll)
            # Timestamp is updated inside check_activities_for_collection

        # Check OpenSea sales if enabled, poll_interval has passed, and collection has opensea_collection_slug
        if OPENSEA_ENABLED and current_ts - last_check_opensea_timestamp[contract] >= poll_interval:
            if coll.get("opensea_collection_slug"):
                print(f"[DEBUG] -> Checking OpenSea Sales for: {coll_name}")
                await check_opensea_sales_for_collection(coll)
                # Timestamp is updated inside check_opensea_sales_for_collection

    print("[DEBUG] check_all_collections() - Finished checking all collections.")

async def check_activities_for_collection(coll_config):
    """
    Check activities (sales, mints, burns) for a collection using Magic Eden API.
    This replaces the old check_sales_for_collection and check_activity_for_collection.
    """
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")
    chain = coll_config.get("chain", "ethereum")

    # Get the last check timestamp (use activity timestamp for unified checking)
    start_ts = last_check_activity_timestamp.get(contract, int(time.time()))
    limit = coll_config.get("activity_limit", 50)

    # Build Magic Eden API URL
    base_url = "https://api-mainnet.magiceden.dev/v4/activity/nft"
    url = (
        f"{base_url}"
        f"?chain={chain}"
        f"&activityTypes[]=TRADE"
        f"&activityTypes[]=MINT"
        f"&activityTypes[]=BURN"
        f"&collectionId={contract}"
        f"&limit={limit}"
        f"&sortBy=timestamp"
        f"&sortDir=desc"
    )

    print(f"[DEBUG][{coll_name}] check_activities_for_collection() - URL: {url}")
    activity_data = await fetch_data(url)
    activities = activity_data.get("activities", [])
    print(f"[DEBUG][{coll_name}] Fetched {len(activities)} activities (before filtering).")

    # Filter by timestamp and reverse to process oldest first
    start_iso = unix_to_iso(start_ts)
    filtered_activities = [act for act in activities if act.get("timestamp", "") >= start_iso]
    filtered_activities.reverse()

    print(f"[DEBUG][{coll_name}] Processing {len(filtered_activities)} activities after timestamp filter.")

    # Process different activity types
    await process_trade_activities(filtered_activities, coll_config)
    await process_mint_activities(filtered_activities, coll_config)
    await process_burn_activities(filtered_activities, coll_config)

    # Update last check timestamp
    last_check_activity_timestamp[contract] = int(time.time())

async def process_trade_activities(activities, coll_config):
    """Process TRADE activities (sales)."""
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")
    zero_addr = coll_config.get("zero_address", "0x0000000000000000000000000000000000000000").lower()
    sales_channel_id = coll_config.get("discord_sales_channel_id", 0)
    sales_channel = bot.get_channel(sales_channel_id) if sales_channel_id else None

    # Get cooldown period in minutes, default to 60 if not specified
    cooldown_minutes = coll_config.get("id_cooldown", 60)
    cooldown_seconds = cooldown_minutes * 60

    new_count = 0
    new_posted = False

    for act in activities:
        if act.get("activityType") != "TRADE":
            continue

        from_addr = act.get("fromAddress", "").lower()
        if from_addr == zero_addr:
            continue

        token_id = act.get("asset", {}).get("tokenId", "???")
        tx_hash = act.get("transactionInfo", {}).get("transactionId", "noTxHash")
        sale_id = f"{token_id}-{tx_hash}"

        # Check if token ID is in cooldown
        current_time = int(time.time())
        last_sale_time = token_id_cooldowns[contract].get(token_id, 0)
        if current_time - last_sale_time < cooldown_seconds:
            print(f"[DEBUG][{coll_name}] Token ID {token_id} is in cooldown, skipping sale")
            continue

        if sale_id not in known_sales[contract]:
            known_sales[contract].append(sale_id)
            max_known_sales = coll_config.get("max_known_sales", 50)
            if len(known_sales[contract]) > max_known_sales:
                known_sales[contract].pop(0)

            # Update token ID cooldown timestamp
            token_id_cooldowns[contract][token_id] = current_time

            if sales_channel:
                embed = await build_sale_embed_me(act, coll_config)
                try:
                    await sales_channel.send(embed=embed)
                except Exception as e:
                    print(f"[DEBUG][{coll_name}] Error sending sale embed: {e}")

            new_count += 1
            new_posted = True

    print(f"[DEBUG][{coll_name}] New sales posted: {new_count}")
    if new_posted:
        save_ids(known_sales[contract], get_sales_file(contract))

async def process_mint_activities(activities, coll_config):
    """Process MINT activities."""
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")
    mint_channel_id = coll_config.get("discord_mint_channel_id", 0)
    mint_channel = bot.get_channel(mint_channel_id) if mint_channel_id else None

    new_count = 0
    new_posted = False

    for act in activities:
        if act.get("activityType") != "MINT":
            continue

        token_id = act.get("asset", {}).get("tokenId", "???")
        tx_hash = act.get("transactionInfo", {}).get("transactionId", "noTxHash")
        mint_id = f"{token_id}-{tx_hash}"

        if mint_id not in known_mints[contract]:
            known_mints[contract].append(mint_id)
            max_mints = coll_config.get("max_known_mints", 100)
            if len(known_mints[contract]) > max_mints:
                known_mints[contract].pop(0)

            if mint_channel:
                try:
                    embed = await build_mint_embed_me(act, coll_config)
                    await mint_channel.send(embed=embed)
                except Exception as e:
                    print(f"[DEBUG][{coll_name}] Error sending mint embed: {e}")

            new_count += 1
            new_posted = True

    print(f"[DEBUG][{coll_name}] New mints posted: {new_count}")
    if new_posted:
        save_ids(known_mints[contract], get_mints_file(contract))

async def process_burn_activities(activities, coll_config):
    """Process BURN activities."""
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")
    burn_channel_id = coll_config.get("discord_burn_channel_id", 0)
    burn_channel = bot.get_channel(burn_channel_id) if burn_channel_id else None

    new_count = 0
    new_posted = False

    for act in activities:
        if act.get("activityType") != "BURN":
            continue

        token_id = act.get("asset", {}).get("tokenId", "???")
        tx_hash = act.get("transactionInfo", {}).get("transactionId", "noTxHash")
        burn_id = f"{token_id}-{tx_hash}"

        if burn_id not in known_burns[contract]:
            known_burns[contract].append(burn_id)
            max_burns = coll_config.get("max_known_burns", 100)
            if len(known_burns[contract]) > max_burns:
                known_burns[contract].pop(0)

            if burn_channel:
                try:
                    embed = await build_burn_embed_me(act, coll_config)
                    await burn_channel.send(embed=embed)
                except Exception as e:
                    print(f"[DEBUG][{coll_name}] Error sending burn embed: {e}")

            new_count += 1
            new_posted = True

    print(f"[DEBUG][{coll_name}] New burns posted: {new_count}")
    if new_posted:
        save_ids(known_burns[contract], get_burns_file(contract))

async def check_opensea_sales_for_collection(coll_config):
    """Check OpenSea for sales events. Only tracks sales, not mints/burns."""
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")

    # Skip if no OpenSea slug configured
    opensea_slug = coll_config.get("opensea_collection_slug")
    if not opensea_slug:
        return

    start_ts = last_check_opensea_timestamp.get(contract, int(time.time()))
    limit = coll_config.get("sales_limit", 50)

    # Build OpenSea API URL
    base_url = "https://api.opensea.io/api/v2/events/collection"
    url = f"{base_url}/{opensea_slug}?limit={limit}&event_type=sale"

    print(f"[DEBUG][{coll_name}] check_opensea_sales_for_collection() - URL: {url}")

    # Fetch with OpenSea API key
    headers = {"accept": "application/json", "x-api-key": OPENSEA_API_KEY}
    opensea_data = await fetch_data_with_headers(url, headers)

    events = opensea_data.get("asset_events", [])
    print(f"[DEBUG][{coll_name}] Fetched {len(events)} OpenSea events (before filtering).")

    # Filter by timestamp and reverse to process oldest first
    filtered_events = [evt for evt in events if evt.get("event_timestamp", 0) >= start_ts]
    filtered_events.reverse()

    print(f"[DEBUG][{coll_name}] Processing {len(filtered_events)} OpenSea sales after timestamp filter.")

    await process_opensea_sale_events(filtered_events, coll_config)

    # Update last check timestamp
    last_check_opensea_timestamp[contract] = int(time.time())

async def process_opensea_sale_events(events, coll_config):
    """Process OpenSea sale events (event_type=sale)."""
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")
    zero_addr = coll_config.get("zero_address", "0x0000000000000000000000000000000000000000").lower()
    sales_channel_id = coll_config.get("discord_sales_channel_id", 0)
    sales_channel = bot.get_channel(sales_channel_id) if sales_channel_id else None

    # Get cooldown period
    cooldown_minutes = coll_config.get("id_cooldown", 60)
    cooldown_seconds = cooldown_minutes * 60

    new_count = 0
    new_posted = False

    for evt in events:
        # Extract sale data from OpenSea format
        # OpenSea uses "nft" field for asset info
        nft = evt.get("nft")
        if not nft:
            continue  # Skip if no nft data

        token_id = nft.get("identifier", "???")

        # Transaction hash is a direct string field in OpenSea sale events
        tx_hash = evt.get("transaction", "noTxHash")

        sale_id = f"{token_id}-{tx_hash}"

        # Seller and buyer are direct string fields in OpenSea sale events
        seller = evt.get("seller", "")
        if seller and seller.lower() == zero_addr:
            continue

        # Check if token ID is in cooldown
        current_time = int(time.time())
        last_sale_time = token_id_cooldowns[contract].get(token_id, 0)
        if current_time - last_sale_time < cooldown_seconds:
            print(f"[DEBUG][{coll_name}] Token ID {token_id} is in cooldown, skipping OpenSea sale")
            continue

        if sale_id not in known_sales[contract]:
            known_sales[contract].append(sale_id)
            max_known_sales = coll_config.get("max_known_sales", 50)
            if len(known_sales[contract]) > max_known_sales:
                known_sales[contract].pop(0)

            # Update token ID cooldown timestamp
            token_id_cooldowns[contract][token_id] = current_time

            if sales_channel:
                embed = await build_opensea_sale_embed(evt, coll_config)
                try:
                    await sales_channel.send(embed=embed)
                except Exception as e:
                    print(f"[DEBUG][{coll_name}] Error sending OpenSea sale embed: {e}")

            new_count += 1
            new_posted = True

    print(f"[DEBUG][{coll_name}] New OpenSea sales posted: {new_count}")
    if new_posted:
        save_ids(known_sales[contract], get_sales_file(contract))

#################################
# Async-Safe Fetch
#################################
def sync_fetch_data(url):
    # Magic Eden API doesn't require authentication
    headers = {"accept": "*/*"}
    start_time = time.time()
    print(f"[DEBUG] (sync) sync_fetch_data() - Starting request to: {url[:200]}...")

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        elapsed = time.time() - start_time
        print(f"[DEBUG] (sync) sync_fetch_data() - Success. Status: {r.status_code}. Time: {elapsed:.2f}s")
        return r.json()
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"[DEBUG] Error fetching data from {url}: {e} (Time: {elapsed:.2f}s)")
        return {}

async def fetch_data(url):
    """Run the blocking sync_fetch_data in a separate thread via asyncio.to_thread()."""
    return await asyncio.to_thread(sync_fetch_data, url)

def sync_fetch_data_with_headers(url, headers):
    """Fetch data with custom headers (for OpenSea API)."""
    start_time = time.time()
    print(f"[DEBUG] (sync) sync_fetch_data_with_headers() - Starting request to: {url[:200]}...")

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        elapsed = time.time() - start_time
        print(f"[DEBUG] (sync) sync_fetch_data_with_headers() - Success. Status: {r.status_code}. Time: {elapsed:.2f}s")
        return r.json()
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"[DEBUG] Error fetching data from {url}: {e} (Time: {elapsed:.2f}s)")
        return {}

async def fetch_data_with_headers(url, headers):
    """Run sync_fetch_data_with_headers in a separate thread."""
    return await asyncio.to_thread(sync_fetch_data_with_headers, url, headers)

def sync_fetch_eth_price():
    """Fetch current ETH/USD spot price from Coinbase."""
    url = "https://api.coinbase.com/v2/prices/ETH-USD/spot"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        price = float(data.get("data", {}).get("amount", 0))
        print(f"[DEBUG] Fetched ETH price: ${price:,.2f}")
        return price
    except Exception as e:
        print(f"[DEBUG] Error fetching ETH price from Coinbase: {e}")
        return 0.0

async def fetch_eth_price():
    """Async wrapper for fetching ETH price."""
    return await asyncio.to_thread(sync_fetch_eth_price)

def weighted_burn_message(burn_list, token_name):
    r = random.random()
    cumulative = 0.0
    for item in burn_list:
        weight = item["weight"]
        msg = item["message"]
        cumulative += weight
        if r < cumulative:
            return msg.replace("{tokenName}", token_name)
    last_msg = burn_list[-1]["message"]
    return last_msg.replace("{tokenName}", token_name)

#################################
# Magic Eden Embed Builders
#################################
async def build_sale_embed_me(activity, coll_config):
    """Build Discord embed for Magic Eden TRADE activity."""
    print(f"[DEBUG] build_sale_embed_me() - Building embed for activity ID: {activity.get('activityId')}")

    asset = activity.get("asset", {})
    token_name = asset.get("name", "Unknown Token")
    token_id = asset.get("tokenId", "???")

    # Get token image from mediaV2
    media_v2 = asset.get("mediaV2", {})
    main_media = media_v2.get("main", {})
    token_image = main_media.get("uri", "")

    # Get price information
    unit_price = activity.get("unitPrice", {})
    amount = unit_price.get("amount", {})
    price_native = float(amount.get("native", 0))
    fiat = amount.get("fiat", {})
    price_usd = float(fiat.get("usd", 0))

    # Get currency symbol
    currency = unit_price.get("currency", {})
    currency_symbol = currency.get("symbol", "ETH")

    seller_address = activity.get("fromAddress", "")
    buyer_address = activity.get("toAddress", "")
    seller_display = await get_ens_or_short(seller_address, coll_config.get("chain", "ethereum"))
    buyer_display = await get_ens_or_short(buyer_address, coll_config.get("chain", "ethereum"))

    tx_info = activity.get("transactionInfo", {})
    tx_hash = tx_info.get("transactionId", "noTxHash")
    transaction_link_base = coll_config.get("transaction_link_base", "https://abscan.org/tx/")
    transaction_link = f"{transaction_link_base}{tx_hash}"

    embed = discord.Embed(
        title=f"{token_name} has been sold!!!",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="Price",
        value=f"{price_native:.5f} {currency_symbol} (${price_usd:,.2f} USD)",
        inline=False
    )
    embed.add_field(name="Seller", value=seller_display, inline=True)
    embed.add_field(name="Buyer", value=buyer_display, inline=True)
    embed.add_field(name="Transaction", value=f"[View on Explorer]({transaction_link})", inline=False)

    if token_image:
        embed.set_image(url=token_image)

    embed.set_footer(text="Powered by Oekaki.io")
    return embed

async def build_mint_embed_me(activity, coll_config):
    """Build Discord embed for Magic Eden MINT activity."""
    print(f"[DEBUG] build_mint_embed_me() - Building embed for activity: {activity.get('activityId')}")

    asset = activity.get("asset", {})
    token_id = asset.get("tokenId", "???")
    token_name = asset.get("name")

    # If the token name is missing, "None", or "???", fallback to "{CollectionName} #{TokenId}"
    if not token_name or token_name.lower() in ("none", "???"):
        token_name = f"{coll_config.get('name', 'Unknown Collection')} #{token_id}"

    to_address = activity.get("toAddress", "")
    to_display = await get_ens_or_short(to_address, coll_config.get("chain", "ethereum"))

    tx_info = activity.get("transactionInfo", {})
    tx_hash = tx_info.get("transactionId", "noTxHash")
    transaction_link_base = coll_config.get("transaction_link_base", "https://abscan.org/tx/")
    transaction_link = f"{transaction_link_base}{tx_hash}"

    # Try to get image from mediaV2 first, fallback to fetching from metadata
    media_v2 = asset.get("mediaV2", {})
    main_media = media_v2.get("main", {})
    token_image_url = main_media.get("uri", "")

    if not token_image_url:
        token_image_url = await fetch_token_image(token_id, coll_config)

    embed = discord.Embed(
        title=f"{token_name} just minted!",
        color=discord.Color.green()
    )
    embed.add_field(
        name="Owner",
        value=to_display,
        inline=True
    )
    embed.add_field(
        name="Transaction",
        value=f"[View on Explorer]({transaction_link})",
        inline=False
    )

    if token_image_url:
        embed.set_image(url=token_image_url)

    embed.set_footer(text="Powered by Oekaki.io")
    return embed

async def build_burn_embed_me(activity, coll_config):
    """Build Discord embed for Magic Eden BURN activity."""
    print(f"[DEBUG] build_burn_embed_me() - Building embed for activity: {activity.get('activityId')}")

    asset = activity.get("asset", {})
    token_id = asset.get("tokenId", "???")
    token_name = asset.get("name") or f"Token #{token_id}"

    burn_messages = coll_config.get("burn_messages", [])
    if not burn_messages:
        burn_messages = [
            {"weight": 1.0, "message": "{tokenName} has been burned!"}
        ]
    burn_title = weighted_burn_message(burn_messages, token_name)

    from_address = activity.get("fromAddress", "")
    from_display = await get_ens_or_short(from_address, coll_config.get("chain", "ethereum"))

    tx_info = activity.get("transactionInfo", {})
    tx_hash = tx_info.get("transactionId", "noTxHash")
    transaction_link_base = coll_config.get("transaction_link_base", "https://abscan.org/tx/")
    transaction_link = f"{transaction_link_base}{tx_hash}"

    # Try to get image from mediaV2 first, fallback to fetching from metadata
    media_v2 = asset.get("mediaV2", {})
    main_media = media_v2.get("main", {})
    token_image_url = main_media.get("uri", "")

    if not token_image_url:
        token_image_url = await fetch_token_image(token_id, coll_config)

    embed = discord.Embed(
        title=burn_title,
        color=discord.Color.red()
    )
    embed.add_field(name="Previous Owner", value=from_display, inline=True)
    embed.add_field(name="Transaction", value=f"[View on Explorer]({transaction_link})", inline=False)

    if token_image_url:
        embed.set_image(url=token_image_url)

    embed.set_footer(text="Powered by Oekaki.io")
    return embed

async def build_opensea_sale_embed(event, coll_config):
    """Build Discord embed for OpenSea sale event (event_type=sale)."""
    print(f"[DEBUG] build_opensea_sale_embed() - Building embed for OpenSea event")

    # OpenSea uses "nft" field for asset info
    nft = event.get("nft", {})
    token_name = nft.get("name", "Unknown Token")
    token_id = nft.get("identifier", "???")
    token_image = nft.get("image_url") or nft.get("display_image_url", "")

    # Get payment information
    payment = event.get("payment", {})
    quantity = payment.get("quantity", "0")
    decimals = payment.get("decimals", 18)
    symbol = payment.get("symbol", "ETH")

    # Convert quantity from wei to native currency
    try:
        price_native = float(quantity) / (10 ** decimals) if quantity and quantity != "0" else 0.0
    except:
        price_native = 0.0

    # Calculate USD price using Coinbase spot price for ETH/WETH
    price_usd = 0.0
    if price_native > 0 and symbol.upper() in ["ETH", "WETH"]:
        eth_price = await fetch_eth_price()
        if eth_price > 0:
            price_usd = price_native * eth_price

    # Seller and buyer are direct string fields in OpenSea sale events
    seller_address = event.get("seller", "")
    buyer_address = event.get("buyer", "")

    seller_display = await get_ens_or_short(seller_address, coll_config.get("chain", "ethereum")) if seller_address else "Unknown"
    buyer_display = await get_ens_or_short(buyer_address, coll_config.get("chain", "ethereum")) if buyer_address else "Unknown"

    # Transaction is a direct string field
    tx_hash = event.get("transaction", "noTxHash")
    transaction_link_base = coll_config.get("transaction_link_base", "https://abscan.org/tx/")
    transaction_link = f"{transaction_link_base}{tx_hash}"

    embed = discord.Embed(
        title=f"{token_name} has been sold!!!",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="Price",
        value=f"{price_native:.5f} {symbol}" + (f" (${price_usd:,.2f} USD)" if price_usd > 0 else ""),
        inline=False
    )
    embed.add_field(name="Seller", value=seller_display, inline=True)
    embed.add_field(name="Buyer", value=buyer_display, inline=True)
    embed.add_field(name="Transaction", value=f"[View on Explorer]({transaction_link})", inline=False)

    if token_image:
        embed.set_image(url=token_image)

    embed.set_footer(text="Powered by Oekaki.io")
    return embed

#################################
# Token Metadata Fetch
#################################
def sync_fetch_token_image(metadata_url):
    try:
        resp = requests.get(metadata_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        image_field = data.get("image", "")
        if not image_field:
            print("[DEBUG] sync_fetch_token_image() - 'image' field empty.")
            return None

        if image_field.startswith("ipfs://"):
            cleaned = image_field.replace("ipfs://", "")
            return f"https://ipfs.io/ipfs/{cleaned}"
        else:
            return image_field
    except Exception as e:
        print(f"[DEBUG] Error fetching token metadata from {metadata_url}: {e}")
        return None

async def fetch_token_image(token_id, coll_config):
    base_uri = coll_config.get("json_base_uri", "").rstrip("/")
    if not base_uri or token_id == "???":
        print("[DEBUG] fetch_token_image() - No base URI or unknown token ID; returning None.")
        return None

    metadata_url = f"{base_uri}/{token_id}"
    print(f"[DEBUG] fetch_token_image() - Fetching metadata from {metadata_url}")
    return await asyncio.to_thread(sync_fetch_token_image, metadata_url)

#################################
# Utility
#################################
def shorten_address(address: str, chars=6) -> str:
    address = address.lower()
    if len(address) > 2 + chars * 2:
        return address[:2 + chars] + "..." + address[-chars:]
    else:
        return address

def iso_to_unix(iso_timestamp: str) -> int:
    """Convert ISO 8601 timestamp to Unix timestamp (seconds)."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except Exception as e:
        print(f"[DEBUG] Error converting ISO timestamp {iso_timestamp}: {e}")
        return 0

def unix_to_iso(unix_timestamp: int) -> str:
    """Convert Unix timestamp (seconds) to ISO 8601 format."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
        return dt.isoformat().replace('+00:00', 'Z')
    except Exception as e:
        print(f"[DEBUG] Error converting Unix timestamp {unix_timestamp}: {e}")
        return ""

random.seed(int(time.time()))

#################################
# Discord Bot Events
#################################
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    print("[DEBUG] on_ready() - Starting check_all_collections task loop...")
    check_all_collections.start()

#################################
# Main
#################################
if __name__ == "__main__":
    print("[DEBUG] Starting bot.run()...")
    bot.run(DISCORD_BOT_TOKEN)
