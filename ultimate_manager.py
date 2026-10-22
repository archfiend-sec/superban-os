import asyncio
import time
import random
import json
import os
import sqlite3
import signal
import sys
import logging
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.types import ChatBannedRights, ChannelParticipantsAdmins
from telethon.errors import (
    FloodWaitError, ChatAdminRequiredError, 
    UserNotParticipantError, PeerFloodError,
    RPCError, UserPrivacyRestrictedError
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════
#   ADVANCED LOGGING SYSTEM
# ═══════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.FileHandler('ultimate_manager.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#   DATABASE MANAGER (ASYNC-SAFE)
# ═══════════════════════════════════════════════════════
class DatabaseManager:
    """SQLite wrapper protected by an asyncio Lock to prevent concurrency corruption."""
    def __init__(self, db_path="ultimate_manager.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.lock = asyncio.Lock()
        self.init_tables()
    
    def init_tables(self):
        self.cursor.executescript('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER,
                username TEXT,
                group_id INTEGER,
                group_name TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN,
                PRIMARY KEY (user_id, group_id)
            );
            
            CREATE TABLE IF NOT EXISTS sessions (
                group_id INTEGER PRIMARY KEY,
                group_name TEXT,
                total_members INTEGER,
                processed INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                status TEXT,
                mode TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                action TEXT,
                count INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        ''')
        self.conn.commit()
    
    async def log_ban(self, user_id, username, group_id, group_name, success):
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT OR REPLACE INTO banned_users 
                    (user_id, username, group_id, group_name, success) 
                    VALUES (?, ?, ?, ?, ?)''',
                    (user_id, username or '', group_id, group_name or '', success)
                )
                self.conn.commit()
            except Exception as e:
                logger.debug(f"DB log_ban error: {e}")
    
    async def start_session(self, group_id, group_name, total, mode):
        async with self.lock:
            self.cursor.execute(
                '''INSERT OR REPLACE INTO sessions 
                (group_id, group_name, total_members, status, mode, started_at) 
                VALUES (?, ?, ?, 'in_progress', ?, ?)''',
                (group_id, group_name, total, mode, datetime.now())
            )
            self.conn.commit()
    
    async def update_session(self, group_id, processed, banned, failed):
        async with self.lock:
            self.cursor.execute(
                '''UPDATE sessions SET processed = ?, banned = ?, failed = ? 
                WHERE group_id = ?''',
                (processed, banned, failed, group_id)
            )
            self.conn.commit()
    
    async def complete_session(self, group_id):
        async with self.lock:
            self.cursor.execute(
                '''UPDATE sessions SET status = 'completed', completed_at = ? 
                WHERE group_id = ?''',
                (datetime.now(), group_id)
            )
            self.conn.commit()
    
    async def get_incomplete_sessions(self):
        async with self.lock:
            self.cursor.execute('SELECT group_id, group_name, total_members, processed, banned, failed, mode FROM sessions WHERE status = "in_progress"')
            return self.cursor.fetchall()
            
    async def get_banned_user_ids(self, group_id):
        """Returns set of user IDs already attempted/banned in this group."""
        async with self.lock:
            self.cursor.execute('SELECT user_id FROM banned_users WHERE group_id = ?', (group_id,))
            rows = self.cursor.fetchall()
            return {r[0] for r in rows}
    
    async def get_stats(self, group_id=None):
        async with self.lock:
            if group_id:
                self.cursor.execute(
                    'SELECT COUNT(*), SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) FROM banned_users WHERE group_id = ?',
                    (group_id,)
                )
            else:
                self.cursor.execute(
                    'SELECT COUNT(*), SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) FROM banned_users'
                )
            return self.cursor.fetchone()
    
    async def log_analytics(self, group_id, action, count):
        async with self.lock:
            self.cursor.execute(
                'INSERT INTO analytics (group_id, action, count) VALUES (?, ?, ?)',
                (group_id, action, count)
            )
            self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════
#   INTELLIGENT USER FILTER
# ═══════════════════════════════════════════════════════
class SmartFilter:
    """Intelligent filtering based on user attributes."""
    
    @staticmethod
    def matches(user, criteria: dict) -> bool:
        """Evaluate a single user against filtering criteria."""
        if not criteria:
            return True
            
        if criteria.get('skip_bots', True) and getattr(user, 'bot', False):
            return False
        if criteria.get('skip_deleted', True) and getattr(user, 'deleted', False):
            return False
        if criteria.get('skip_premium', False) and getattr(user, 'premium', False):
            return False
        if criteria.get('only_premium', False) and not getattr(user, 'premium', False):
            return False
        if criteria.get('only_no_username', False) and getattr(user, 'username', None):
            return False
        if criteria.get('only_with_username', False) and not getattr(user, 'username', None):
            return False
        if criteria.get('skip_verified', True) and getattr(user, 'verified', False):
            return False
            
        return True

    @staticmethod
    def filter_by_criteria(users, criteria):
        """Advanced batch filtering."""
        return [user for user in users if SmartFilter.matches(user, criteria)]


# ═══════════════════════════════════════════════════════
#   ADAPTIVE RATE LIMITER
# ═══════════════════════════════════════════════════════
class AdaptiveRateLimiter:
    """Auto-adjusts speed based on flood waits."""
    
    def __init__(self, initial_delay=0.5, min_delay=0.1, max_delay=10.0):
        self.current_delay = initial_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.flood_count = 0
        self.success_count = 0
    
    async def wait(self):
        if self.current_delay > 0:
            jitter = random.uniform(0.85, 1.25)
            await asyncio.sleep(self.current_delay * jitter)
    
    def on_flood(self, seconds):
        """Adapt when flood wait occurs."""
        self.flood_count += 1
        self.current_delay = min(max(self.current_delay * 1.8, 1.0), self.max_delay)
        logger.warning(f"⚠️ Flood #{self.flood_count} - Delay increased → {self.current_delay:.2f}s")
    
    def on_success(self):
        """Gradually speed up on continuous success."""
        self.success_count += 1
        if self.success_count % 30 == 0:
            self.current_delay = max(self.current_delay * 0.92, self.min_delay)
            logger.info(f"⚡ Performance boost - Delay reduced → {self.current_delay:.2f}s")


# ═══════════════════════════════════════════════════════
#   SUPER ADVANCED BAN MANAGER
# ═══════════════════════════════════════════════════════
class SuperBanManager:
    def __init__(self, api_id, api_hash, phone_number, session_count=1):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        
        # Load string session if available to avoid any prompts
        string_session = os.environ.get("TELEGRAM_STRING_SESSION", "").strip()
        if string_session:
            self.client = TelegramClient(StringSession(string_session), api_id, api_hash)
        else:
            os.makedirs("sessions", exist_ok=True)
            self.client = TelegramClient(os.path.abspath("sessions/superban"), api_id, api_hash)
            
        self.clients = [self.client]
        
        # Database
        self.db = DatabaseManager()
        
        # Rate limiter
        self.rate_limiter = AdaptiveRateLimiter(initial_delay=1.0, min_delay=0.2, max_delay=12.0)
        
        # Ban rights
        self.ban_rights = ChatBannedRights(
            until_date=None,
            view_messages=True, send_messages=True, send_media=True,
            send_stickers=True, send_gifs=True, send_games=True,
            send_inline=True, embed_links=True,
        )
        
        # Tuned modes with realistic delays to prevent account termination
        self.modes = {
            'stealth':    {'concurrent': 1,  'delay': 3.0,  'desc': 'Slowest, safest, lowest flood risk'},
            'normal':     {'concurrent': 3,  'delay': 1.5,  'desc': 'Balanced for standard accounts'},
            'fast':       {'concurrent': 8,  'delay': 0.8,  'desc': 'Speedy batch execution'},
            'turbo':      {'concurrent': 15, 'delay': 0.4,  'desc': 'Aggressive, multiple workers'},
            'nuclear':    {'concurrent': 25, 'delay': 0.2,  'desc': 'High aggression (FloodWait likely)'},
            'annihilate': {'concurrent': 40, 'delay': 0.1,  'desc': 'Maximum throughput limit push'},
        }
        
        self.current_mode = 'normal'
        self.running = True
        self.stats = {
            'banned': 0, 'failed': 0, 'skipped': 0,
            'start_time': None, 'floods': 0
        }
        
        # Signal handler
        signal.signal(signal.SIGINT, self.signal_handler)
    
    def signal_handler(self, sig, frame):
        print("\n⚠️ Graceful shutdown requested... Finishing in-flight tasks.")
        self.running = False

    async def start(self):
        """Start session with persistent authorization."""
        await self.client.connect()
        if await self.client.is_user_authorized():
            print("⚡ Restored session instantly! Zero login prompts required.")
        else:
            print(f"\n🔐 Initial one-time authentication for {self.phone_number}...")
            print("ℹ️  Telegram will send a code to your Telegram app / SMS.")
            await self.client.start(phone=self.phone_number)
            print("✅ Verified!")
            
            # Export string session to .env permanently
            try:
                temp_s = StringSession()
                temp_s.set_dc(self.client.session.dc_id, self.client.session.server_address, self.client.session.port)
                temp_s.auth_key = self.client.session.auth_key
                s_str = temp_s.save()
                if s_str:
                    with open(".env", "a", encoding="utf-8") as f:
                        f.write(f"\nTELEGRAM_STRING_SESSION={s_str}\n")
                    os.environ["TELEGRAM_STRING_SESSION"] = s_str
                    print("💾 Session permanently saved to .env! You will NEVER have to log in again.\n")
            except Exception as e:
                logger.debug(f"Session save notice: {e}")
        
        # Check for incomplete sessions
        incomplete = await self.db.get_incomplete_sessions()
        if incomplete:
            print(f"\n🔄 Found {len(incomplete)} incomplete session(s) in database.")
            for s in incomplete:
                print(f"   • Group ID: {s[0]} ({s[1]}) - Processed: {s[3]}/{s[2]}")

    async def get_members_smart(self, group_entity, limit=5000, criteria=None):
        """Smart member collection using SmartFilter criteria."""
        criteria = criteria or {'skip_bots': True, 'skip_deleted': True}
        
        print(f"🔍 Collecting members (limit: {limit})...")
        users = []
        skipped = 0
        
        self_user = await self.client.get_me()
        self_id = self_user.id if self_user else None

        async for user in self.client.iter_participants(group_entity, limit=limit):
            if not self.running:
                break
            if user.id == self_id:
                continue
            
            if not SmartFilter.matches(user, criteria):
                skipped += 1
                continue
                
            users.append(user)
            if len(users) % 250 == 0:
                print(f"   📊 Collected {len(users)} eligible users so far...")
        
        print(f"✅ Collection complete: {len(users)} targets ({skipped} filtered out)")
        return users, skipped

    async def get_admins(self, group_entity):
        """Get admin IDs to prevent accidental banning."""
        admin_ids = set()
        try:
            async for admin in self.client.iter_participants(
                group_entity, filter=ChannelParticipantsAdmins
            ):
                admin_ids.add(admin.id)
            print(f"🛡️ Protected {len(admin_ids)} admin account(s)")
        except Exception as e:
            logger.warning(f"Could not fetch admins: {e}")
        return admin_ids

    async def ban_single(self, client, group_entity, user, group_id, group_name, max_retries=3):
        """Ban one user with iterative FloodWait backoff and safety bounds."""
        for attempt in range(max_retries):
            if not self.running:
                return False
            try:
                await client(EditBannedRequest(
                    channel=group_entity,
                    participant=user.id,
                    banned_rights=self.ban_rights
                ))
                await self.db.log_ban(
                    user.id, getattr(user, 'username', ''),
                    group_id, group_name, True
                )
                self.rate_limiter.on_success()
                return True
            
            except FloodWaitError as e:
                self.stats['floods'] += 1
                self.rate_limiter.on_flood(e.seconds)
                
                # Safety check: if flood wait is huge (e.g. > 180s), avoid hanging
                if e.seconds > 180:
                    logger.error(f"🛑 Critical FloodWait ({e.seconds}s). Halting operation to protect account.")
                    self.running = False
                    return False
                    
                logger.warning(f"⏳ FloodWait: sleeping {e.seconds}s (attempt {attempt + 1}/{max_retries})")
                jitter = random.uniform(1.0, 3.0)
                await asyncio.sleep(e.seconds + jitter)
                
            except (UserNotParticipantError, UserPrivacyRestrictedError):
                await self.db.log_ban(user.id, getattr(user, 'username', ''), group_id, group_name, False)
                return False
            
            except ChatAdminRequiredError:
                print("❌ Admin privileges lost or missing for this operation!")
                self.running = False
                return False
            
            except Exception as e:
                logger.debug(f"Ban failed for user {user.id}: {e}")
                await self.db.log_ban(user.id, getattr(user, 'username', ''), group_id, group_name, False)
                return False
                
        return False

    async def bulk_ban(self, group_entity, users, skip_admins=True, already_processed=0):
        """Execute bulk ban with load balancing and progress tracking."""
        mode_cfg = self.modes[self.current_mode]
        self.rate_limiter.current_delay = mode_cfg['delay']
        
        # Admin protection
        admin_ids = set()
        if skip_admins:
            admin_ids = await self.get_admins(group_entity)
            
        self_user = await self.client.get_me()
        self_id = self_user.id if self_user else None
        
        targets = [u for u in users if u.id not in admin_ids and u.id != self_id]
        
        group_id = group_entity.id
        group_name = getattr(group_entity, 'title', 'Unknown')
        total = len(targets)
        
        print(f"\n🎯 INITIATING BULK BAN")
        print(f"   Target Group: {group_name} (ID: {group_id})")
        print(f"   Mode:         {self.current_mode.upper()} ({mode_cfg['desc']})")
        print(f"   Total Users:  {total}")
        print(f"   Concurrency:  {mode_cfg['concurrent']}")
        print("═" * 55)
        
        await self.db.start_session(group_id, group_name, total + already_processed, self.current_mode)
        self.stats['start_time'] = time.time()
        
        semaphore = asyncio.Semaphore(mode_cfg['concurrent'])
        banned_count = [0]
        failed_count = [0]
        processed = [already_processed]
        progress_lock = asyncio.Lock()
        
        async def worker(idx, user):
            if not self.running:
                return
            async with semaphore:
                # Distribute tasks round-robin across available client sessions
                client = self.clients[idx % len(self.clients)]
                success = await self.ban_single(client, group_entity, user, group_id, group_name)
                
                async with progress_lock:
                    if success:
                        banned_count[0] += 1
                    else:
                        failed_count[0] += 1
                    processed[0] += 1
                    curr_proc = processed[0]
                
                # Status report every 20 actions
                if curr_proc % 20 == 0 or curr_proc == total + already_processed:
                    elapsed = max(time.time() - self.stats['start_time'], 0.1)
                    speed = (curr_proc - already_processed) / elapsed
                    remaining = total - (curr_proc - already_processed)
                    eta = remaining / speed if speed > 0 else 0
                    pct = (curr_proc / (total + already_processed)) * 100
                    
                    print(
                        f"   [{pct:5.1f}%] {curr_proc}/{total + already_processed} | "
                        f"✅ {banned_count[0]} | ❌ {failed_count[0]} | "
                        f"⚡ {speed:.1f}/s | ETA: {eta:.0f}s"
                    )
                    await self.db.update_session(group_id, curr_proc, banned_count[0], failed_count[0])
                
                await self.rate_limiter.wait()
        
        tasks = [worker(i, u) for i, u in enumerate(targets)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        elapsed = max(time.time() - self.stats['start_time'], 0.1)
        if self.running:
            await self.db.complete_session(group_id)
        await self.db.log_analytics(group_id, 'bulk_ban', banned_count[0])
        
        print(f"\n{'═'*55}")
        print(f"🏁 OPERATION FINISHED")
        print(f"   Status:       {'Completed' if self.running else 'Suspended / Stopped'}")
        print(f"   ✅ Banned:     {banned_count[0]}")
        print(f"   ❌ Failed:     {failed_count[0]}")
        print(f"   ⏱️  Duration:   {elapsed:.2f}s")
        print(f"   ⚡ Avg Speed:  {banned_count[0]/elapsed:.1f} users/s")
        print(f"   🌊 FloodWaits: {self.stats['floods']}")
        print(f"{'═'*55}\n")
        
        return banned_count[0]

    async def delete_history(self, group_entity, days=7):
        """Batch delete message history using delete_messages (up to 100 per call)."""
        print(f"🗑️ Deleting message history from the last {days} days...")
        cutoff = datetime.now() - timedelta(days=days)
        batch = []
        total_deleted = 0
        
        try:
            async for msg in self.client.iter_messages(group_entity, offset_date=cutoff):
                if not self.running:
                    break
                batch.append(msg.id)
                if len(batch) >= 100:
                    try:
                        await self.client.delete_messages(group_entity, batch)
                        total_deleted += len(batch)
                        print(f"   🗑️ Purged {total_deleted} messages...")
                        batch.clear()
                        await asyncio.sleep(1.0)
                    except FloodWaitError as e:
                        logger.warning(f"Delete FloodWait: {e.seconds}s")
                        await asyncio.sleep(e.seconds + 1)
                    except Exception as e:
                        logger.debug(f"Batch delete error: {e}")
                        batch.clear()
                        
            if batch and self.running:
                await self.client.delete_messages(group_entity, batch)
                total_deleted += len(batch)
                
            print(f"✅ Finished! Purged {total_deleted} total messages.")
        except Exception as e:
            print(f"❌ Deletion encountered an error: {e}")

    async def get_analytics(self, group_entity):
        """Analyze group member demographics."""
        title = getattr(group_entity, 'title', 'Selected Group')
        print(f"\n📊 Scanning demographics for {title}...")
        
        total = 0
        bots = 0
        premium = 0
        deleted = 0
        with_username = 0
        
        async for user in self.client.iter_participants(group_entity):
            total += 1
            if getattr(user, 'bot', False): bots += 1
            if getattr(user, 'premium', False): premium += 1
            if getattr(user, 'deleted', False): deleted += 1
            if getattr(user, 'username', None): with_username += 1
            
            if total % 500 == 0:
                print(f"   📊 Scanned {total} members...")
        
        print(f"\n{'═'*55}")
        print(f"📊 GROUP ANALYTICS - {title}")
        print(f"{'═'*55}")
        print(f"   👥 Total Members:      {total}")
        print(f"   🤖 Bots:               {bots} ({(bots/total*100) if total else 0:.1f}%)")
        print(f"   ⭐ Premium Accounts:   {premium} ({(premium/total*100) if total else 0:.1f}%)")
        print(f"   👻 Deleted / Ghost:    {deleted} ({(deleted/total*100) if total else 0:.1f}%)")
        print(f"   📝 With Username:      {with_username}")
        print(f"   🔍 Without Username:   {total - with_username - deleted}")
        print(f"{'═'*55}\n")
        
        return {
            'total': total, 'bots': bots, 'premium': premium,
            'deleted': deleted, 'with_username': with_username
        }

    async def close(self):
        """Gracefully disconnect all sessions and database connections."""
        for client in self.clients:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass
        self.db.close()


# ═══════════════════════════════════════════════════════
#   INTERACTIVE UI
# ═══════════════════════════════════════════════════════
class InteractiveUI:
    def __init__(self, manager: SuperBanManager):
        self.manager = manager
    
    def print_banner(self):
        print("""
╔═══════════════════════════════════════════════════════╗
║   🚀 SUPER ADVANCED GROUP MANAGER v3.1                ║
║   🛡️ Concurrency-Safe • Adaptive • Batch-Optimized   ║
╚═══════════════════════════════════════════════════════╝
        """)
    
    def print_menu(self):
        print("""
┌─── MAIN CONTROL PANEL ────────────────────────────────┐
│  1. 🎯 Ban Members (select mode & filters)            │
│  2. 📊 Group Analytics                                │
│  3. 🗑️  Delete Message History (Safe batch purge)      │
│  4. 📈 View Global Statistics                         │
│  5. ⚙️  Tune Delays & Rate Limiter                    │
│  6. 🔄 Resume Incomplete Session                      │
│  7. 🚪 Exit                                           │
└───────────────────────────────────────────────────────┘
        """)
    
    def print_modes(self):
        print("""
┌─── BAN SPEED MODES ───────────────────────────────────┐
│  1. 🕵️  Stealth    - 1 worker, 3.0s delay (Safest)    │
│  2. ⚡ Normal     - 3 workers, 1.5s delay (Balanced) │
│  3. 🚀 Fast       - 8 workers, 0.8s delay (Quick)    │
│  4. 💨 Turbo      - 15 workers, 0.4s delay           │
│  5. 💣 Nuclear    - 25 workers, 0.2s delay           │
│  6. ☢️  Annihilate - 40 workers, 0.1s delay           │
└───────────────────────────────────────────────────────┘
        """)
    
    async def run(self):
        self.print_banner()
        while True:
            self.print_menu()
            choice = input("👉 Select option (1-7): ").strip()
            
            try:
                if choice == "1":
                    await self.ban_flow()
                elif choice == "2":
                    await self.analytics_flow()
                elif choice == "3":
                    await self.delete_flow()
                elif choice == "4":
                    await self.stats_flow()
                elif choice == "5":
                    await self.settings_flow()
                elif choice == "6":
                    await self.resume_flow()
                elif choice == "7":
                    print("👋 Exiting Super Group Manager.")
                    return
                else:
                    print("❌ Invalid option. Enter 1 to 7.")
            except KeyboardInterrupt:
                print("\n⚠️ Operation cancelled by user.")
            except Exception as e:
                logger.error(f"UI Error: {e}", exc_info=True)

    async def select_group(self):
        dialogs = await self.manager.client.get_dialogs()
        groups = [d for d in dialogs if d.is_group or d.is_channel]
        
        if not groups:
            print("❌ No accessible groups or channels found on this account.")
            return None
        
        print("\n📋 Available Groups / Supergroups:")
        for i, g in enumerate(groups[:25], 1):
            print(f"  {i:2d}. {g.name} (ID: {g.id})")
        
        try:
            choice = int(input(f"\n👉 Select group (1-{min(25, len(groups))}): ")) - 1
            if 0 <= choice < len(groups):
                return groups[choice].entity
        except ValueError:
            pass
        print("❌ Invalid selection.")
        return None

    def configure_filters(self):
        """Prompt user for filtering criteria."""
        print("\n🔍 Configure Member Filters:")
        skip_bots = input("Skip bots? [Y/n]: ").strip().lower() != 'n'
        skip_deleted = input("Skip deleted accounts? [Y/n]: ").strip().lower() != 'n'
        skip_premium = input("Skip Telegram Premium users? [y/N]: ").strip().lower() == 'y'
        only_no_user = input("Target ONLY users without a @username? [y/N]: ").strip().lower() == 'y'
        
        return {
            'skip_bots': skip_bots,
            'skip_deleted': skip_deleted,
            'skip_premium': skip_premium,
            'only_no_username': only_no_user,
            'skip_verified': True
        }

    async def ban_flow(self):
        group = await self.select_group()
        if not group:
            return
        
        self.print_modes()
        mode_choice = input("👉 Select ban mode (1-6) [default: 2]: ").strip()
        modes = {
            "1": "stealth", "2": "normal", "3": "fast",
            "4": "turbo", "5": "nuclear", "6": "annihilate"
        }
        mode = modes.get(mode_choice, "normal")
        self.manager.current_mode = mode
        print(f"✅ Mode set to: {mode.upper()}")
        
        limit_str = input("📊 Maximum members to scan (default 5000): ").strip()
        limit = int(limit_str) if limit_str.isdigit() else 5000
        
        criteria = self.configure_filters()
        users, skipped = await self.manager.get_members_smart(group, limit=limit, criteria=criteria)
        self.manager.stats['skipped'] = skipped
        
        if not users:
            print("❌ No matching users found to ban.")
            return
        
        print(f"\n⚠️  PRE-FLIGHT CONFIRMATION:")
        print(f"   Target:  {getattr(group, 'title', 'Group')}")
        print(f"   Count:   {len(users)} users")
        print(f"   Mode:    {mode.upper()}")
        if mode in ['nuclear', 'annihilate']:
            print("💀 WARNING: Aggressive modes carry a high risk of FloodWait or account restriction!")
        
        confirm = input("❓ Proceed with ban? (type 'yes' to confirm): ").strip().lower()
        if confirm != 'yes':
            print("❌ Cancelled by user.")
            return
        
        self.manager.running = True
        await self.manager.bulk_ban(group, users, skip_admins=True)

    async def analytics_flow(self):
        group = await self.select_group()
        if group:
            await self.manager.get_analytics(group)

    async def delete_flow(self):
        group = await self.select_group()
        if not group:
            return
        days_str = input("🗓️  Delete messages from last how many days? (default 7): ").strip()
        days = int(days_str) if days_str.isdigit() else 7
        confirm = input(f"⚠️  Confirm deleting {days} days of messages? (type 'yes'): ").strip().lower()
        if confirm == 'yes':
            self.manager.running = True
            await self.manager.delete_history(group, days=days)

    async def stats_flow(self):
        total, success = await self.manager.db.get_stats()
        print(f"\n📈 GLOBAL DATABASE STATS")
        print(f"   Total Bans Attempted: {total or 0}")
        print(f"   Successful Bans:      {success or 0}")
        print(f"   Success Rate:         {(success/total*100) if total else 0:.1f}%")
        print(f"   Session FloodWaits:   {self.manager.stats['floods']}")
        print(f"   Current Delay:        {self.manager.rate_limiter.current_delay:.2f}s")

    async def settings_flow(self):
        rl = self.manager.rate_limiter
        print(f"\n⚙️  Current Delay: {rl.current_delay:.2f}s")
        print(f"⚙️  Min Delay:     {rl.min_delay:.2f}s")
        print(f"⚙️  Max Delay:     {rl.max_delay:.2f}s")
        
        val = input("Enter new minimum delay in seconds (or press Enter to keep): ").strip()
        if val:
            try:
                rl.min_delay = max(0.05, float(val))
                print(f"✅ Updated Min Delay to {rl.min_delay}s")
            except ValueError:
                print("❌ Invalid numeric input.")

    async def resume_flow(self):
        """Find an incomplete session, filter out already-banned user IDs, and resume."""
        incomplete = await self.manager.db.get_incomplete_sessions()
        if not incomplete:
            print("✅ No incomplete sessions found in database.")
            return
            
        print(f"\n🔄 Incomplete Sessions:")
        for idx, s in enumerate(incomplete, 1):
            print(f"  {idx}. {s[1]} (Group ID: {s[0]}) - Progress: {s[3]}/{s[2]} | Mode: {s[6]}")
            
        try:
            choice = int(input(f"\n👉 Select session to resume (1-{len(incomplete)}): ")) - 1
            if not (0 <= choice < len(incomplete)):
                print("❌ Invalid selection.")
                return
        except ValueError:
            return
            
        selected = incomplete[choice]
        group_id, group_name, total_members, processed, banned, failed, mode = selected
        
        # Resolve group entity
        try:
            group_entity = await self.manager.client.get_entity(group_id)
        except Exception as e:
            print(f"❌ Failed to fetch group entity for ID {group_id}: {e}")
            return
            
        print(f"\n🔄 Resuming session for '{group_name}'...")
        already_banned_ids = await self.manager.db.get_banned_user_ids(group_id)
        print(f"   Found {len(already_banned_ids)} users already processed in database.")
        
        # Collect remaining members
        all_users, _ = await self.manager.get_members_smart(group_entity, limit=total_members)
        remaining_targets = [u for u in all_users if u.id not in already_banned_ids]
        
        print(f"   Remaining users to process: {len(remaining_targets)}")
        if not remaining_targets:
            print("✅ All users from this group have already been processed.")
            await self.manager.db.complete_session(group_id)
            return
            
        confirm = input(f"❓ Proceed to ban remaining {len(remaining_targets)} users? (type 'yes'): ").strip().lower()
        if confirm != 'yes':
            print("❌ Resume cancelled.")
            return
            
        self.manager.current_mode = mode if mode in self.manager.modes else 'normal'
        self.manager.running = True
        await self.manager.bulk_ban(
            group_entity, remaining_targets, 
            skip_admins=True, already_processed=len(already_banned_ids)
        )


# ═══════════════════════════════════════════════════════
#   CREDENTIALS & ENTRY POINT
# ═══════════════════════════════════════════════════════
CONFIG_FILE = "ultimate_config.json"

def load_credentials():
    """Load credentials from environment variables or local JSON config."""
    env_api_id = os.environ.get("TELEGRAM_API_ID")
    env_api_hash = os.environ.get("TELEGRAM_API_HASH")
    env_phone = os.environ.get("TELEGRAM_PHONE")
    
    if env_api_id and env_api_hash and env_phone:
        return {'api_id': int(env_api_id), 'api_hash': env_api_hash, 'phone': env_phone}
        
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_credentials(api_id, api_hash, phone):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'api_id': api_id, 'api_hash': api_hash, 'phone': phone}, f, indent=2)
        print(f"✅ Credentials saved to {CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Failed to save credentials: {e}")

async def main():
    print("🚀 SUPER ADVANCED GROUP MANAGER v3.1")
    
    creds = load_credentials()
    if creds:
        print(f"✅ Credentials detected for account: {creds.get('phone')}")
        use_saved = input("Use these credentials? [Y/n]: ").strip().lower()
        if use_saved == 'n':
            creds = None
            
    if not creds:
        default_id = os.environ.get("TELEGRAM_API_ID", "").strip()
        default_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
        default_phone = os.environ.get("TELEGRAM_PHONE", "").strip()

        id_prompt = f"Enter Telegram API ID [{default_id}]: " if default_id else "Enter Telegram API ID: "
        api_id_str = input(id_prompt).strip() or default_id
        
        hash_prompt = f"Enter Telegram API Hash [{default_hash}]: " if default_hash else "Enter Telegram API Hash: "
        api_hash = input(hash_prompt).strip() or default_hash
        
        phone_prompt = f"Enter Phone Number (with country code, e.g. +123456789) [{default_phone}]: " if default_phone else "Enter Phone Number (with country code, e.g. +123456789): "
        phone = input(phone_prompt).strip() or default_phone
        
        if not api_id_str.isdigit():
            print("❌ Error: API ID must be an integer.")
            return
            
        api_id = int(api_id_str)
        if input("💾 Save credentials locally? [y/N]: ").strip().lower() == 'y':
            save_credentials(api_id, api_hash, phone)
        creds = {'api_id': api_id, 'api_hash': api_hash, 'phone': phone}

    manager = SuperBanManager(creds['api_id'], creds['api_hash'], creds['phone'])
    
    try:
        await manager.start()
        ui = InteractiveUI(manager)
        await ui.run()
    except Exception as e:
        logger.critical(f"Fatal error encountered: {e}", exc_info=True)
    finally:
        await manager.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
