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

def load_file_secrets(path):
    """Load multiple Reservoir API keys, one per line."""
    print(f"[DEBUG] Loading multiple secrets from {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find file: {path}")
    with open(path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    print(f"[DEBUG] Successfully loaded {len(lines)} Reservoir API keys.")
    return lines

RESERVOIR_API_KEYS = load_file_secrets("reservoir_api.keys")
DISCORD_BOT_TOKEN = load_file_secret("discord_bot.token")

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

def get_sales_file(contract_address):
    return f"known_sales_{contract_address}.txt"

def get_mints_file(contract_address):
    return f"known_mints_{contract_address}.txt"

def get_burns_file(contract_address):
    return f"known_burns_{contract_address}.txt"

def load_ids(filename):
    print(f"[DEBUG] Loading IDs from {filename}")
    if not os.path.exists(filename):
        print(f"[DEBUG] File does not exist: {filename}, returning empty list.")
        return []
    with open(filename, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    print(f"[DEBUG] Loaded {len(lines)} IDs from {filename}")
    return lines

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

        # Check Sales if poll_interval has passed
        if current_ts - last_check_sales_timestamp[contract] >= poll_interval:
            print(f"[DEBUG] -> Checking Sales for: {coll_name}")
            await check_sales_for_collection(coll)
            last_check_sales_timestamp[contract] = current_ts

        # Check Activity if poll_interval has passed
        if current_ts - last_check_activity_timestamp[contract] >= poll_interval:
            print(f"[DEBUG] -> Checking Activity for: {coll_name}")
            await check_activity_for_collection(coll)
            last_check_activity_timestamp[contract] = current_ts

    print("[DEBUG] check_all_collections() - Finished checking all collections.")

async def check_sales_for_collection(coll_config):
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")

    start_ts = last_check_sales_timestamp[contract]
    end_ts = int(time.time())

    limit = coll_config.get("sales_limit", 50)
    reservoir_base = coll_config["reservoir_api_base_url"].rstrip("/")
    url = (
        f"{reservoir_base}/sales/v6"
        f"?contract={contract}"
        f"&includeTokenMetadata=true"
        f"&includeDeleted=true"
        f"&sortBy=time"
        f"&sortDirection=desc"
        f"&startTimestamp={start_ts}"
        f"&endTimestamp={end_ts}"
        f"&limit={limit}"
    )

    print(f"[DEBUG][{coll_name}] check_sales_for_collection() - URL: {url}")
    sales_data = await fetch_data(url)
    sales = sales_data.get("sales", [])
    print(f"[DEBUG][{coll_name}] Fetched {len(sales)} sales (before filtering).")

    sales.reverse()

    new_posted = False
    zero_addr = coll_config.get("zero_address", "0x0000000000000000000000000000000000000000").lower()

    sales_channel_id = coll_config.get("discord_sales_channel_id", 0)
    sales_channel = bot.get_channel(sales_channel_id) if sales_channel_id else None

    # Get cooldown period in minutes, default to 60 if not specified
    cooldown_minutes = coll_config.get("id_cooldown", 60)
    cooldown_seconds = cooldown_minutes * 60

    new_count = 0
    for sale in sales:
        from_addr = sale["from"].lower()
        sale_id = sale["id"]
        token_id = sale["token"].get("tokenId", "???")

        if from_addr == zero_addr:
            continue

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
                embed = await build_sale_embed(sale, coll_config)
                try:
                    await sales_channel.send(embed=embed)
                except Exception as e:
                    print(f"[DEBUG][{coll_name}] Error sending sale embed: {e}")

            new_count += 1
            new_posted = True

    print(f"[DEBUG][{coll_name}] New sales posted: {new_count}")
    if new_posted:
        save_ids(known_sales[contract], get_sales_file(contract))

async def check_activity_for_collection(coll_config):
    contract = coll_config["contract_address"].lower()
    coll_name = coll_config.get("name", "Unknown")

    start_ts = last_check_activity_timestamp[contract]
    end_ts = int(time.time())
    limit = coll_config.get("activity_limit", 50)

    reservoir_base = coll_config["reservoir_api_base_url"].rstrip("/")
    url = (
        f"{reservoir_base}/collections/activity/v6"
        f"?collection={contract}"
        f"&limit={limit}"
        f"&sortBy=eventTimestamp"
        f"&startTimestamp={start_ts}"
        f"&endTimestamp={end_ts}"
        f"&includeMetadata=true"
    )

    print(f"[DEBUG][{coll_name}] check_activity_for_collection() - URL: {url}")
    activity_data = await fetch_data(url)
    activities = activity_data.get("activities", [])
    print(f"[DEBUG][{coll_name}] Fetched {len(activities)} activity items (before filtering).")

    activities.reverse()

    minted_posted = False
    burned_posted = False

    burn_addr = coll_config.get("burn_address", "0x000000000000000000000000000000000000dEaD").lower()
    zero_addr = coll_config.get("zero_address", "0x0000000000000000000000000000000000000000").lower()

    mint_channel_id = coll_config.get("discord_mint_channel_id", 0)
    burn_channel_id = coll_config.get("discord_burn_channel_id", 0)

    mint_channel = bot.get_channel(mint_channel_id) if mint_channel_id else None
    burn_channel = bot.get_channel(burn_channel_id) if burn_channel_id else None

    mint_count = 0
    burn_count = 0

    for act in activities:
        act_type = act["type"]

        # MINT
        if act_type == "mint":
            token_id = act["token"].get("tokenId", "???")
            tx_hash = act.get("txHash", "noTxHash")
            mint_id = f"{token_id}-{tx_hash}"

            if mint_id not in known_mints[contract]:
                known_mints[contract].append(mint_id)
                max_mints = coll_config.get("max_known_mints", 100)
                if len(known_mints[contract]) > max_mints:
                    known_mints[contract].pop(0)

                if mint_channel:
                    try:
                        embed = await build_mint_embed(act, coll_config)
                        await mint_channel.send(embed=embed)
                    except Exception as e:
                        print(f"[DEBUG][{coll_name}] Error sending mint embed: {e}")

                mint_count += 1
                minted_posted = True

        # BURN
        elif act_type == "transfer" and act["toAddress"].lower() == burn_addr:
            token_id = act["token"].get("tokenId", "???")
            tx_hash = act.get("txHash", "noTxHash")
            burn_id = f"{token_id}-{tx_hash}"

            if burn_id not in known_burns[contract]:
                known_burns[contract].append(burn_id)
                max_burns = coll_config.get("max_known_burns", 100)
                if len(known_burns[contract]) > max_burns:
                    known_burns[contract].pop(0)

                if burn_channel:
                    try:
                        embed = await build_burn_embed(act, coll_config)
                        await burn_channel.send(embed=embed)
                    except Exception as e:
                        print(f"[DEBUG][{coll_name}] Error sending burn embed: {e}")

                burn_count += 1
                burned_posted = True

    print(f"[DEBUG][{coll_name}] New mints posted: {mint_count}, new burns posted: {burn_count}")

    if minted_posted:
        save_ids(known_mints[contract], get_mints_file(contract))
    if burned_posted:
        save_ids(known_burns[contract], get_burns_file(contract))

#################################
# Async-Safe Fetch
#################################
def sync_fetch_data(url):
    chosen_key = random.choice(RESERVOIR_API_KEYS)
    headers = {"accept": "*/*", "x-api-key": chosen_key}
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

#################################
# Embed Builders
#################################
async def build_sale_embed(sale, coll_config):
    print(f"[DEBUG] build_sale_embed() - Building embed for sale ID: {sale.get('id')}")
    token_data = sale["token"]
    token_name = token_data.get("name", "Unknown Token")
    token_image = token_data.get("image", "")
    price_eth = sale["price"]["amount"]["decimal"]
    price_usd = sale["price"]["amount"].get("usd", 0)

    seller_address = sale["from"]
    buyer_address = sale["to"]
    seller_display = await get_ens_or_short(seller_address, coll_config.get("chain", "ethereum"))
    buyer_display = await get_ens_or_short(buyer_address, coll_config.get("chain", "ethereum"))

    tx_hash = sale.get("txHash", "noTxHash")
    transaction_link_base = coll_config.get("transaction_link_base", "https://abscan.org/tx/")
    transaction_link = f"{transaction_link_base}{tx_hash}"

    embed = discord.Embed(
        title=f"{token_name} has been sold!!!",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="Price",
        value=f"{price_eth:.5f} ETH (${price_usd:,.2f} USD)",
        inline=False
    )
    embed.add_field(name="Seller", value=seller_display, inline=True)
    embed.add_field(name="Buyer", value=buyer_display, inline=True)
    embed.add_field(name="Transaction", value=f"[View on Explorer]({transaction_link})", inline=False)

    if token_image:
        embed.set_image(url=token_image)

    embed.set_footer(text="Powered by Oekaki.io")
    return embed

async def build_mint_embed(activity, coll_config):
    print(f"[DEBUG] build_mint_embed() - Building embed for activity: {activity.get('txHash')}")

    token_data = activity["token"]
    token_id = token_data.get("tokenId", "???")
    token_name = token_data.get("tokenName")

    # If the token name is missing, "None", or "???", fallback to "{CollectionName} #{TokenId}"
    if not token_name or token_name.lower() in ("none", "???"):
        # Use collection name + # + token_id
        token_name = f"{coll_config.get('name', 'Unknown Collection')} #{token_id}"

    to_address = activity["toAddress"]
    to_display = await get_ens_or_short(to_address, coll_config.get("chain", "ethereum"))

    tx_hash = activity.get("txHash", "noTxHash")
    transaction_link_base = coll_config.get("transaction_link_base", "https://abscan.org/tx/")
    transaction_link = f"{transaction_link_base}{tx_hash}"

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


async def build_burn_embed(activity, coll_config):
    print(f"[DEBUG] build_burn_embed() - Building embed for activity: {activity.get('txHash')}")
    token_data = activity["token"]
    token_id = token_data.get("tokenId", "???")
    token_name = token_data.get("tokenName") or f"Survivor #{token_id}"

    burn_messages = coll_config.get("burn_messages", [])
    if not burn_messages:
        burn_messages = [
            {"weight": 1.0, "message": "{tokenName} has been burned!"}
        ]
    burn_title = weighted_burn_message(burn_messages, token_name)

    from_address = activity["fromAddress"]
    from_display = await get_ens_or_short(from_address, coll_config.get("chain", "ethereum"))

    tx_hash = activity.get("txHash", "noTxHash")
    transaction_link_base = coll_config.get("transaction_link_base", "https://abscan.org/tx/")
    transaction_link = f"{transaction_link_base}{tx_hash}"

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
