import asyncio
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set, Optional

DB_FILE = Path(__file__).resolve().parent.parent / "ultimate_manager.db"

class DatabaseManager:
    """Async-safe, high-concurrency SQLite persistence layer with WAL mode."""
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or DB_FILE)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
        self.cursor = self.conn.cursor()
        self.lock = asyncio.Lock()
        
        # Enable Write-Ahead Logging (WAL) for maximum stability & zero database locks
        self.cursor.execute("PRAGMA journal_mode=WAL;")
        self.cursor.execute("PRAGMA synchronous=NORMAL;")
        self.cursor.execute("PRAGMA busy_timeout=10000;")
        self.conn.commit()
        
        self.init_schema()

    def init_schema(self):
        self.cursor.executescript('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                group_id INTEGER,
                group_name TEXT,
                action TEXT DEFAULT 'ban',
                reason TEXT,
                duration INTEGER DEFAULT 0, -- 0 for permanent, or seconds
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN,
                PRIMARY KEY (user_id, group_id, action)
            );
            
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                group_id INTEGER,
                group_name TEXT,
                action TEXT DEFAULT 'ban',
                total_members INTEGER,
                processed INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status TEXT, -- 'in_progress', 'paused', 'aborted', 'completed'
                mode TEXT,
                started_at TIMESTAMP,
                updated_at TIMESTAMP,
                completed_at TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS whitelist (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                note TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS warnings (
                user_id INTEGER,
                group_id INTEGER,
                reason TEXT,
                strikes INTEGER DEFAULT 1,
                last_warned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, group_id)
            );
            
            CREATE TABLE IF NOT EXISTS scraped_leads (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                is_premium BOOLEAN,
                last_seen TEXT,
                source_group_id INTEGER,
                source_group_title TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                level TEXT,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS defcon_config (
                group_id INTEGER PRIMARY KEY,
                level INTEGER DEFAULT 5, -- 5: Peace, 4: Guarded, 3: Elevated, 2: Raid Imminent, 1: Nuclear Lockdown
                slowmode_seconds INTEGER DEFAULT 0,
                auto_captcha BOOLEAN DEFAULT 0,
                auto_kick_spammers BOOLEAN DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ai_threat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                user_id INTEGER,
                username TEXT,
                threat_type TEXT, -- 'CRYPTO_DRAINER', 'IMPERSONATOR', 'ZERO_DAY_PHISH', 'TOXIC_RAID'
                confidence INTEGER DEFAULT 95,
                reason TEXT,
                action_taken TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS broadcast_campaigns (
                id TEXT PRIMARY KEY,
                title TEXT,
                group_id INTEGER,
                message_template TEXT,
                total_targets INTEGER DEFAULT 0,
                sent_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending', -- 'pending', 'running', 'paused', 'completed'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS activity_analytics (
                group_id INTEGER,
                hour INTEGER, -- 0-23
                day INTEGER,  -- 0-6 (Mon-Sun)
                message_count INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, hour, day)
            );

            -- ═══════════════════════════════════════════════════════
            --   SOVEREIGN INTELLIGENCE & GHOST VAULT SUITE
            -- ═══════════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS intercepted_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                group_title TEXT,
                message_id INTEGER,
                user_id INTEGER,
                full_name TEXT,
                username TEXT,
                text TEXT,
                media_type TEXT,
                media_path TEXT,
                file_name TEXT,
                file_size INTEGER DEFAULT 0,
                file_hash TEXT,
                is_deleted BOOLEAN DEFAULT 0,
                is_edited BOOLEAN DEFAULT 0,
                original_text TEXT,
                date_sent TIMESTAMP,
                date_deleted TIMESTAMP,
                date_edited TIMESTAMP,
                UNIQUE(group_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS surveillance_watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT, -- 'keyword' or 'user'
                value TEXT,
                note TEXT,
                alert_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(target_type, value)
            );

            CREATE TABLE IF NOT EXISTS surveillance_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER,
                target_type TEXT,
                trigger_val TEXT,
                group_id INTEGER,
                group_title TEXT,
                user_id INTEGER,
                full_name TEXT,
                username TEXT,
                message_text TEXT,
                action_context TEXT, -- 'message' or 'vc_speaking' or 'vc_join'
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_cross_profiles (
                user_id INTEGER PRIMARY KEY,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                current_name TEXT,
                current_username TEXT,
                name_history TEXT DEFAULT '[]',
                username_history TEXT DEFAULT '[]',
                spotted_groups TEXT DEFAULT '[]',
                message_count INTEGER DEFAULT 0,
                vc_participations INTEGER DEFAULT 0,
                is_premium BOOLEAN DEFAULT 0,
                is_verified BOOLEAN DEFAULT 0,
                phone TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS vc_talker_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                call_id TEXT,
                user_id INTEGER,
                full_name TEXT,
                username TEXT,
                total_spoke_sec REAL DEFAULT 0,
                last_spoke_at TIMESTAMP,
                UNIQUE(group_id, call_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS extracted_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                group_title TEXT,
                message_id INTEGER,
                sender_id INTEGER,
                sender_name TEXT,
                entity_type TEXT, -- 'crypto_sol', 'crypto_eth', 'crypto_btc', 'crypto_ton', 'url', 'tg_invite', 'cashtag', 'email'
                entity_value TEXT,
                context TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_entities_type ON extracted_entities(entity_type);
            CREATE INDEX IF NOT EXISTS idx_entities_val ON extracted_entities(entity_value);
            CREATE INDEX IF NOT EXISTS idx_entities_grp ON extracted_entities(group_id);

            CREATE TABLE IF NOT EXISTS interaction_network (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                source_user_id INTEGER,
                source_name TEXT,
                target_user_id INTEGER,
                target_name TEXT,
                interaction_type TEXT, -- 'reply', 'mention'
                message_id INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_inter_grp ON interaction_network(group_id);
            CREATE INDEX IF NOT EXISTS idx_inter_src ON interaction_network(source_user_id);
            CREATE INDEX IF NOT EXISTS idx_inter_tgt ON interaction_network(target_user_id);

            CREATE TABLE IF NOT EXISTS siphon_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT,
                match_type TEXT, -- 'keyword', 'crypto', 'user', 'all_deleted'
                match_value TEXT,
                destination TEXT DEFAULT 'me',
                is_active BOOLEAN DEFAULT 1,
                siphoned_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS siphon_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                rule_name TEXT,
                group_id INTEGER,
                group_title TEXT,
                sender_name TEXT,
                matched_text TEXT,
                destination TEXT DEFAULT 'me',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS operator_clearances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                pin_hash TEXT NOT NULL,
                role TEXT DEFAULT 'operator', -- 'owner' or 'operator'
                status TEXT DEFAULT 'active', -- 'active' or 'revoked'
                assigned_by TEXT DEFAULT 'archfiend000',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS security_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                username TEXT,
                client_ip TEXT,
                detail TEXT,
                severity TEXT DEFAULT 'INFO',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        self.conn.commit()

        # Idempotent migration for Ghost Media Vault columns in intercepted_messages
        for col_name, col_type in [
            ("media_path", "TEXT"),
            ("file_name", "TEXT"),
            ("file_size", "INTEGER DEFAULT 0"),
            ("file_hash", "TEXT"),
        ]:
            try:
                self.cursor.execute(f"ALTER TABLE intercepted_messages ADD COLUMN {col_name} {col_type};")
                self.conn.commit()
            except Exception:
                pass

        # Seed or sync master owner @archfiend000 clearance
        try:
            from core.config import Config
            owner_hash = Config.hash_pin(Config.DEFAULT_MASTER_OWNER_PIN)
            self.cursor.execute(
                '''INSERT OR IGNORE INTO operator_clearances (username, pin_hash, role, status, assigned_by)
                   VALUES ('archfiend000', ?, 'owner', 'active', 'system')''',
                (owner_hash,)
            )
            self.conn.commit()
        except Exception:
            pass

    async def log_action(self, user_id: int, username: str, first_name: str, group_id: int, 
                         group_name: str, action: str, success: bool, reason: str = "", duration: int = 0):
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT OR REPLACE INTO banned_users 
                    (user_id, username, first_name, group_id, group_name, action, success, reason, duration, banned_at) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (user_id, username or '', first_name or '', group_id, group_name or '', 
                     action, success, reason, duration, datetime.now())
                )
                self.conn.commit()
            except Exception:
                pass

    async def add_warning(self, user_id: int, group_id: int, reason: str = "Spam / Policy Violation") -> int:
        async with self.lock:
            self.cursor.execute('SELECT strikes FROM warnings WHERE user_id = ? AND group_id = ?', (user_id, group_id))
            row = self.cursor.fetchone()
            strikes = (row[0] + 1) if row else 1
            self.cursor.execute(
                '''INSERT OR REPLACE INTO warnings (user_id, group_id, reason, strikes, last_warned_at) 
                VALUES (?, ?, ?, ?, ?)''',
                (user_id, group_id, reason, strikes, datetime.now())
            )
            self.conn.commit()
            return strikes

    async def get_warnings(self, group_id: int) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute('SELECT user_id, reason, strikes, last_warned_at FROM warnings WHERE group_id = ? ORDER BY strikes DESC', (group_id,))
            rows = self.cursor.fetchall()
            return [{"user_id": r[0], "reason": r[1], "strikes": r[2], "last_warned_at": r[3]} for r in rows]

    async def clear_warning(self, user_id: int, group_id: int):
        async with self.lock:
            self.cursor.execute('DELETE FROM warnings WHERE user_id = ? AND group_id = ?', (user_id, group_id))
            self.conn.commit()

    async def save_scraped_leads(self, leads: List[Dict[str, Any]], group_id: int, group_title: str) -> int:
        async with self.lock:
            saved = 0
            for l in leads:
                try:
                    self.cursor.execute(
                        '''INSERT OR REPLACE INTO scraped_leads 
                        (user_id, username, full_name, is_premium, last_seen, source_group_id, source_group_title, scraped_at) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                        (l["id"], l.get("username", ""), l.get("full_name", ""), l.get("is_premium", False), 
                         l.get("last_seen", ""), group_id, group_title, datetime.now())
                    )
                    saved += 1
                except Exception:
                    pass
            self.conn.commit()
            return saved

    async def get_scraped_leads(self, limit: int = 200) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute('SELECT user_id, username, full_name, is_premium, last_seen, source_group_title, scraped_at FROM scraped_leads ORDER BY scraped_at DESC LIMIT ?', (limit,))
            rows = self.cursor.fetchall()
            return [{
                "user_id": r[0], "username": r[1], "full_name": r[2],
                "is_premium": bool(r[3]), "last_seen": r[4], "source_group": r[5], "scraped_at": r[6]
            } for r in rows]

    async def start_session(self, session_id: str, group_id: int, group_name: str, 
                            total: int, mode: str, action: str = "ban"):
        async with self.lock:
            now = datetime.now()
            self.cursor.execute(
                '''INSERT OR REPLACE INTO sessions 
                (session_id, group_id, group_name, action, total_members, status, mode, started_at, updated_at) 
                VALUES (?, ?, ?, ?, ?, 'in_progress', ?, ?, ?)''',
                (session_id, group_id, group_name, action, total, mode, now, now)
            )
            self.conn.commit()

    async def update_session_progress(self, session_id: str, processed: int, success: int, failed: int, status: str = "in_progress"):
        async with self.lock:
            self.cursor.execute(
                '''UPDATE sessions 
                SET processed = ?, success_count = ?, failed_count = ?, status = ?, updated_at = ? 
                WHERE session_id = ?''',
                (processed, success, failed, status, datetime.now(), session_id)
            )
            self.conn.commit()

    async def complete_session(self, session_id: str, status: str = "completed"):
        async with self.lock:
            self.cursor.execute(
                '''UPDATE sessions 
                SET status = ?, completed_at = ?, updated_at = ? 
                WHERE session_id = ?''',
                (status, datetime.now(), datetime.now(), session_id)
            )
            self.conn.commit()

    async def get_processed_user_ids(self, group_id: int, action: str = "ban") -> Set[int]:
        async with self.lock:
            self.cursor.execute('SELECT user_id FROM banned_users WHERE group_id = ? AND action = ?', (group_id, action))
            return {r[0] for r in self.cursor.fetchall()}

    async def get_incomplete_sessions(self) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute(
                'SELECT session_id, group_id, group_name, action, total_members, processed, success_count, failed_count, status, mode, started_at '
                'FROM sessions WHERE status IN ("in_progress", "paused") ORDER BY updated_at DESC'
            )
            columns = [c[0] for c in self.cursor.description]
            return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

    async def get_whitelist(self) -> Set[int]:
        async with self.lock:
            self.cursor.execute('SELECT user_id FROM whitelist')
            return {r[0] for r in self.cursor.fetchall()}

    async def add_to_whitelist(self, user_id: int, username: str = "", note: str = ""):
        async with self.lock:
            self.cursor.execute(
                'INSERT OR REPLACE INTO whitelist (user_id, username, note) VALUES (?, ?, ?)',
                (user_id, username, note)
            )
            self.conn.commit()

    async def remove_from_whitelist(self, user_id: int):
        async with self.lock:
            self.cursor.execute('DELETE FROM whitelist WHERE user_id = ?', (user_id,))
            self.conn.commit()

    async def get_stats(self) -> Dict[str, Any]:
        async with self.lock:
            self.cursor.execute('SELECT COUNT(*), SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) FROM banned_users')
            total, success = self.cursor.fetchone()
            self.cursor.execute('SELECT COUNT(*) FROM sessions WHERE status="completed"')
            completed_sessions = self.cursor.fetchone()[0]
            self.cursor.execute('SELECT COUNT(*) FROM whitelist')
            whitelist_count = self.cursor.fetchone()[0]
            self.cursor.execute('SELECT COUNT(*) FROM scraped_leads')
            scraped_leads_count = self.cursor.fetchone()[0]
            
            return {
                "total_actions": total or 0,
                "successful_actions": success or 0,
                "failed_actions": (total or 0) - (success or 0),
                "completed_sessions": completed_sessions,
                "whitelisted_users": whitelist_count,
                "scraped_leads": scraped_leads_count
            }

    async def log_audit(self, group_id: int, level: str, message: str):
        async with self.lock:
            try:
                self.cursor.execute(
                    'INSERT INTO audit_log (group_id, level, message) VALUES (?, ?, ?)',
                    (group_id, level, message)
                )
                self.conn.commit()
            except Exception:
                pass

    async def set_defcon(self, group_id: int, level: int, slowmode_seconds: int = 0) -> int:
        async with self.lock:
            self.cursor.execute(
                '''INSERT INTO defcon_config (group_id, level, slowmode_seconds, updated_at) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET 
                    level=excluded.level, 
                    slowmode_seconds=excluded.slowmode_seconds, 
                    updated_at=excluded.updated_at''',
                (group_id, level, slowmode_seconds, datetime.now())
            )
            self.conn.commit()
            return level

    async def get_defcon(self, group_id: int) -> Dict[str, Any]:
        async with self.lock:
            self.cursor.execute('SELECT level, slowmode_seconds, auto_captcha, auto_kick_spammers, updated_at FROM defcon_config WHERE group_id = ?', (group_id,))
            row = self.cursor.fetchone()
            if row:
                return {
                    "group_id": group_id, "level": row[0], "slowmode_seconds": row[1],
                    "auto_captcha": bool(row[2]), "auto_kick_spammers": bool(row[3]),
                    "updated_at": str(row[4])
                }
            return {
                "group_id": group_id, "level": 5, "slowmode_seconds": 0,
                "auto_captcha": False, "auto_kick_spammers": True,
                "updated_at": str(datetime.now())
            }

    async def log_ai_threat(self, group_id: int, user_id: int, username: str, threat_type: str, confidence: int, reason: str, action: str):
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT INTO ai_threat_logs (group_id, user_id, username, threat_type, confidence, reason, action_taken, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (group_id, user_id, username or '', threat_type, confidence, reason, action, datetime.now())
                )
                self.conn.commit()
            except Exception:
                pass

    async def get_ai_threat_logs(self, group_id: int, limit: int = 30) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute(
                '''SELECT id, user_id, username, threat_type, confidence, reason, action_taken, timestamp 
                FROM ai_threat_logs WHERE group_id = ? ORDER BY id DESC LIMIT ?''',
                (group_id, limit)
            )
            rows = self.cursor.fetchall()
            return [{
                "id": r[0], "user_id": r[1], "username": r[2], "threat_type": r[3],
                "confidence": r[4], "reason": r[5], "action_taken": r[6], "timestamp": str(r[7])
            } for r in rows]

    async def record_activity(self, group_id: int, hour: int, day: int, increment: int = 1):
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT INTO activity_analytics (group_id, hour, day, message_count)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(group_id, hour, day) DO UPDATE SET message_count = message_count + ?''',
                    (group_id, hour, day, increment, increment)
                )
                self.conn.commit()
            except Exception:
                pass

    async def get_activity_heatmap(self, group_id: int) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute(
                'SELECT hour, day, message_count FROM activity_analytics WHERE group_id = ?',
                (group_id,)
            )
            rows = self.cursor.fetchall()
            return [{"hour": r[0], "day": r[1], "count": r[2]} for r in rows]

    async def create_broadcast(self, campaign_id: str, title: str, group_id: int, template: str, total: int):
        async with self.lock:
            self.cursor.execute(
                '''INSERT OR REPLACE INTO broadcast_campaigns 
                (id, title, group_id, message_template, total_targets, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'running', ?)''',
                (campaign_id, title, group_id, template, total, datetime.now())
            )
            self.conn.commit()

    async def update_broadcast_progress(self, campaign_id: str, sent: int, failed: int, status: str = 'running'):
        async with self.lock:
            now = datetime.now() if status == 'completed' else None
            self.cursor.execute(
                '''UPDATE broadcast_campaigns 
                SET sent_count = ?, failed_count = ?, status = ?, completed_at = COALESCE(?, completed_at)
                WHERE id = ?''',
                (sent, failed, status, now, campaign_id)
            )
            self.conn.commit()

    async def get_broadcast_campaigns(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute(
                '''SELECT id, title, group_id, total_targets, sent_count, failed_count, status, created_at, completed_at
                FROM broadcast_campaigns ORDER BY created_at DESC LIMIT ?''',
                (limit,)
            )
            rows = self.cursor.fetchall()
            return [{
                "id": r[0], "title": r[1], "group_id": r[2], "total": r[3],
                "sent": r[4], "failed": r[5], "status": r[6], "created_at": str(r[7]), "completed_at": str(r[8])
            } for r in rows]

    # ═══════════════════════════════════════════════════════
    #   GHOST VAULT & MESSAGE INTERCEPTION (ANTI-DELETE/EDIT)
    # ═══════════════════════════════════════════════════════
    async def save_intercepted_message(self, group_id: int, group_title: str, message_id: int,
                                       user_id: int, full_name: str, username: str, 
                                       text: str, media_type: str = "",
                                       media_path: str = "", file_name: str = "",
                                       file_size: int = 0, file_hash: str = ""):
        """Store incoming message snapshot for anti-delete/anti-edit forensics."""
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT OR IGNORE INTO intercepted_messages
                    (group_id, group_title, message_id, user_id, full_name, username, text, media_type,
                     media_path, file_name, file_size, file_hash, date_sent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (group_id, group_title or "", message_id, user_id, full_name or "Anonymous",
                     username or "", text or "", media_type or "",
                     media_path or "", file_name or "", file_size or 0, file_hash or "", datetime.now())
                )
                self.conn.commit()
            except Exception:
                pass

    async def mark_messages_deleted(self, group_id: int, message_ids: List[int]) -> List[Dict[str, Any]]:
        """Intercept deleted messages and mark them in the Ghost Vault."""
        if not message_ids:
            return []
        intercepted = []
        async with self.lock:
            try:
                placeholders = ','.join('?' * len(message_ids))
                self.cursor.execute(
                    f'''SELECT message_id, user_id, full_name, username, text, media_type,
                               media_path, file_name, file_size, file_hash, date_sent
                    FROM intercepted_messages WHERE group_id = ? AND message_id IN ({placeholders})''',
                    [group_id] + list(message_ids)
                )
                rows = self.cursor.fetchall()
                for r in rows:
                    intercepted.append({
                        "message_id": r[0],
                        "user_id": r[1],
                        "full_name": r[2],
                        "username": r[3],
                        "text": r[4],
                        "media_type": r[5],
                        "media_path": r[6] or "",
                        "file_name": r[7] or "",
                        "file_size": r[8] or 0,
                        "file_hash": r[9] or "",
                        "date_sent": str(r[10]),
                        "date_deleted": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })
                
                self.cursor.execute(
                    f'''UPDATE intercepted_messages 
                    SET is_deleted = 1, date_deleted = ?
                    WHERE group_id = ? AND message_id IN ({placeholders})''',
                    [datetime.now(), group_id] + list(message_ids)
                )
                self.conn.commit()
            except Exception:
                pass
        return intercepted

    async def mark_message_edited(self, group_id: int, message_id: int, new_text: str) -> Optional[Dict[str, Any]]:
        """Track edited messages preserving the original unedited text."""
        result = None
        async with self.lock:
            try:
                self.cursor.execute(
                    '''SELECT text, user_id, full_name, username, date_sent 
                    FROM intercepted_messages WHERE group_id = ? AND message_id = ?''',
                    (group_id, message_id)
                )
                row = self.cursor.fetchone()
                if row:
                    orig_text = row[0]
                    self.cursor.execute(
                        '''UPDATE intercepted_messages
                        SET is_edited = 1, original_text = COALESCE(original_text, ?), text = ?, date_edited = ?
                        WHERE group_id = ? AND message_id = ?''',
                        (orig_text, new_text, datetime.now(), group_id, message_id)
                    )
                    self.conn.commit()
                    result = {
                        "message_id": message_id,
                        "user_id": row[1],
                        "full_name": row[2],
                        "username": row[3],
                        "original_text": orig_text,
                        "new_text": new_text,
                        "date_sent": str(row[4]),
                        "date_edited": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
            except Exception:
                pass
        return result

    async def get_ghost_messages(self, group_id: Optional[int] = None, filter_mode: str = 'all', limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve deleted and edited messages from the Ghost Vault."""
        async with self.lock:
            query = "SELECT id, group_id, group_title, message_id, user_id, full_name, username, text, media_type, media_path, file_name, file_size, file_hash, is_deleted, is_edited, original_text, date_sent, date_deleted, date_edited FROM intercepted_messages WHERE "
            params = []
            
            conds = []
            if group_id:
                conds.append("group_id = ?")
                params.append(group_id)
            
            if filter_mode == 'deleted':
                conds.append("is_deleted = 1")
            elif filter_mode == 'edited':
                conds.append("is_edited = 1")
            else:
                conds.append("(is_deleted = 1 OR is_edited = 1)")
                
            query += " AND ".join(conds)
            query += " ORDER BY COALESCE(date_deleted, date_edited, date_sent) DESC LIMIT ?"
            params.append(limit)

            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
            return [{
                "id": r[0],
                "group_id": r[1],
                "group_title": r[2],
                "message_id": r[3],
                "user_id": r[4],
                "full_name": r[5],
                "username": r[6],
                "text": r[7],
                "media_type": r[8],
                "media_path": r[9] or "",
                "file_name": r[10] or "",
                "file_size": r[11] or 0,
                "file_hash": r[12] or "",
                "is_deleted": bool(r[13]),
                "is_edited": bool(r[14]),
                "original_text": r[15],
                "date_sent": str(r[16]) if r[16] else "",
                "date_deleted": str(r[17]) if r[17] else "",
                "date_edited": str(r[18]) if r[18] else ""
            } for r in rows]

    async def get_ghost_message_by_id(self, ghost_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a specific intercepted message by its database primary key ID."""
        async with self.lock:
            sql = "SELECT id, group_id, group_title, message_id, user_id, full_name, username, text, media_type, media_path, file_name, file_size, file_hash, is_deleted, is_edited, original_text, date_sent, date_deleted, date_edited FROM intercepted_messages WHERE id = ?"
            self.cursor.execute(sql, (ghost_id,))
            r = self.cursor.fetchone()
            if not r:
                return None
            return {
                "id": r[0],
                "group_id": r[1],
                "group_title": r[2],
                "message_id": r[3],
                "user_id": r[4],
                "full_name": r[5],
                "username": r[6],
                "text": r[7],
                "media_type": r[8],
                "media_path": r[9] or "",
                "file_name": r[10] or "",
                "file_size": r[11] or 0,
                "file_hash": r[12] or "",
                "is_deleted": bool(r[13]),
                "is_edited": bool(r[14]),
                "original_text": r[15],
                "date_sent": str(r[16]) if r[16] else "",
                "date_deleted": str(r[17]) if r[17] else "",
                "date_edited": str(r[18]) if r[18] else ""
            }

    # ═══════════════════════════════════════════════════════
    #   SHADOW NETWORK & SYNDICATE SHILL RING ANALYSIS
    # ═══════════════════════════════════════════════════════
    async def detect_syndicate_rings(self, time_window_sec: int = 120, min_cooccurrences: int = 2) -> List[Dict[str, Any]]:
        """Detect coordinated shill rings and user syndicates who post within tight time windows across groups."""
        async with self.lock:
            try:
                sql = '''
                    SELECT 
                        m1.user_id AS u1_id, m1.full_name AS u1_name, m1.username AS u1_user,
                        m2.user_id AS u2_id, m2.full_name AS u2_name, m2.username AS u2_user,
                        m1.group_id, m1.group_title,
                        COUNT(*) AS co_count,
                        MIN(m1.date_sent) as first_seen,
                        MAX(m1.date_sent) as last_seen
                    FROM intercepted_messages m1
                    JOIN intercepted_messages m2 ON m1.group_id = m2.group_id
                        AND m1.user_id < m2.user_id
                        AND abs(strftime('%s', m1.date_sent) - strftime('%s', m2.date_sent)) <= ?
                    GROUP BY m1.user_id, m2.user_id, m1.group_id
                    HAVING co_count >= ?
                    ORDER BY co_count DESC LIMIT 50
                '''
                self.cursor.execute(sql, (time_window_sec, min_cooccurrences))
                rows = self.cursor.fetchall()
                
                pair_map = {}
                for r in rows:
                    u1_id, u1_name, u1_user, u2_id, u2_name, u2_user, gid, gtitle, count, fseen, lseen = r
                    key = (u1_id, u2_id)
                    if key not in pair_map:
                        pair_map[key] = {
                            "user1": {"id": u1_id, "name": u1_name, "username": u1_user},
                            "user2": {"id": u2_id, "name": u2_name, "username": u2_user},
                            "groups": [],
                            "total_cooccurrences": 0,
                            "first_seen": str(fseen),
                            "last_seen": str(lseen)
                        }
                    pair_map[key]["total_cooccurrences"] += count
                    pair_map[key]["groups"].append({"id": gid, "title": gtitle or f"Group {gid}", "count": count})
                
                syndicates = []
                for (u1, u2), data in pair_map.items():
                    grp_count = len(data["groups"])
                    total_co = data["total_cooccurrences"]
                    score = min(99, int(35 + (grp_count * 20) + (total_co * 5)))
                    
                    self.cursor.execute('''
                        SELECT DISTINCT e1.entity_value, e1.entity_type
                        FROM extracted_entities e1
                        JOIN extracted_entities e2 ON e1.entity_value = e2.entity_value
                        WHERE e1.sender_id = ? AND e2.sender_id = ?
                        LIMIT 5
                    ''', (u1, u2))
                    shared = [{"value": er[0], "type": er[1]} for er in self.cursor.fetchall()]
                    if shared:
                        score = min(99, score + 15)
                    
                    data["syndicate_score"] = score
                    data["shared_entities"] = shared
                    data["classification"] = "COORDINATED SHILL RING" if score >= 75 else "SUSPECTED ASSOCIATES"
                    syndicates.append(data)
                
                syndicates.sort(key=lambda s: s["syndicate_score"], reverse=True)
                return syndicates
            except Exception as e:
                return []

    # ═══════════════════════════════════════════════════════
    #   CIRCADIAN SLEEP-CYCLE & TIMEZONE ESTIMATION PROFILER
    # ═══════════════════════════════════════════════════════
    async def get_user_sleep_cycle_profile(self, user_id: int) -> Dict[str, Any]:
        """Calculate circadian rhythm, hourly UTC message activity distribution, and estimated timezone."""
        async with self.lock:
            try:
                self.cursor.execute('''
                    SELECT strftime('%H', date_sent) AS hr, COUNT(*) 
                    FROM intercepted_messages 
                    WHERE user_id = ? 
                    GROUP BY hr
                ''', (user_id,))
                rows = self.cursor.fetchall()
                
                hour_counts = {int(h): c for h, c in rows if h is not None}
                full_dist = [hour_counts.get(h, 0) for h in range(24)]
                total_msgs = sum(full_dist)
                
                if total_msgs < 3:
                    return {
                        "user_id": user_id,
                        "hourly_distribution": full_dist,
                        "total_messages": total_msgs,
                        "sleep_window_utc": "Insufficient telemetry",
                        "estimated_timezone": "Unknown (Requires >= 3 messages)",
                        "confidence": 10,
                        "peak_hour_utc": 0
                    }
                
                # Find minimum 7-hour contiguous inactivity window (sleep valley)
                min_sum = float('inf')
                best_start = 0
                window_size = 7
                for start in range(24):
                    cur_sum = sum(full_dist[(start + offset) % 24] for offset in range(window_size))
                    if cur_sum < min_sum:
                        min_sum = cur_sum
                        best_start = start
                
                sleep_start = best_start
                sleep_end = (best_start + window_size) % 24
                
                # Human circadian midpoint around 03:30 local time
                midpoint_utc = (sleep_start + (window_size / 2.0)) % 24
                raw_offset = 3.5 - midpoint_utc
                while raw_offset > 14:
                    raw_offset -= 24
                while raw_offset < -12:
                    raw_offset += 24
                
                offset_round = round(raw_offset * 2) / 2
                sign = "+" if offset_round >= 0 else "-"
                abs_val = abs(offset_round)
                hrs = int(abs_val)
                mins = int((abs_val - hrs) * 60)
                tz_str = f"UTC{sign}{hrs:02d}:{mins:02d}"
                
                tz_region = "Global"
                if offset_round in [-5, -4]:
                    tz_region = "Americas (EST/EDT/Atlantic)"
                elif offset_round in [-8, -7]:
                    tz_region = "Americas (PST/PDT/MST)"
                elif offset_round in [0, 1]:
                    tz_region = "Europe / UK / W. Africa"
                elif offset_round in [2, 3]:
                    tz_region = "Eastern Europe / Middle East / E. Africa"
                elif offset_round == 5.5:
                    tz_region = "India / South Asia (IST)"
                elif offset_round in [7, 8]:
                    tz_region = "East Asia / China / Singapore / WA"
                elif offset_round in [9, 10]:
                    tz_region = "Japan / Korea / Eastern Australia"
                
                peak_hr = full_dist.index(max(full_dist))
                confidence = min(95, int(40 + min(45, total_msgs * 2)))
                
                return {
                    "user_id": user_id,
                    "hourly_distribution": full_dist,
                    "total_messages": total_msgs,
                    "sleep_window_utc": f"{sleep_start:02d}:00 - {sleep_end:02d}:00 UTC",
                    "estimated_timezone": f"{tz_str} ({tz_region})",
                    "utc_offset_hours": offset_round,
                    "confidence": confidence,
                    "peak_hour_utc": peak_hr
                }
            except Exception as e:
                return {
                    "user_id": user_id,
                    "hourly_distribution": [0]*24,
                    "total_messages": 0,
                    "sleep_window_utc": "Error",
                    "estimated_timezone": "Error",
                    "confidence": 0,
                    "peak_hour_utc": 0
                }

    # ═══════════════════════════════════════════════════════
    #   SURVEILLANCE WATCHLIST & REAL-TIME ALERTS
    # ═══════════════════════════════════════════════════════
    async def add_watchlist_item(self, target_type: str, value: str, note: str = "") -> Dict[str, Any]:
        """Add a keyword or target user ID / username to active surveillance."""
        async with self.lock:
            val_clean = str(value).strip().lower()
            self.cursor.execute(
                '''INSERT OR REPLACE INTO surveillance_watchlists (target_type, value, note, created_at)
                VALUES (?, ?, ?, ?)''',
                (target_type, val_clean, note, datetime.now())
            )
            self.conn.commit()
            return {"target_type": target_type, "value": val_clean, "note": note}

    async def get_watchlist(self) -> List[Dict[str, Any]]:
        """Fetch all active surveillance watchlist rules."""
        async with self.lock:
            self.cursor.execute('SELECT id, target_type, value, note, alert_count, created_at FROM surveillance_watchlists ORDER BY id DESC')
            rows = self.cursor.fetchall()
            return [{
                "id": r[0], "target_type": r[1], "value": r[2], "note": r[3],
                "alert_count": r[4], "created_at": str(r[5])
            } for r in rows]

    async def delete_watchlist_item(self, item_id: int) -> bool:
        """Remove a watchlist rule."""
        async with self.lock:
            self.cursor.execute('DELETE FROM surveillance_watchlists WHERE id = ?', (item_id,))
            self.conn.commit()
            return self.cursor.rowcount > 0

    async def log_surveillance_alert(self, watchlist_id: Optional[int], target_type: str, trigger_val: str,
                                     group_id: int, group_title: str, user_id: int, 
                                     full_name: str, username: str, message_text: str, action_context: str = "message"):
        """Record an alert when surveillance target is detected."""
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT INTO surveillance_alerts
                    (watchlist_id, target_type, trigger_val, group_id, group_title, user_id, full_name, username, message_text, action_context, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (watchlist_id or 0, target_type, trigger_val, group_id, group_title or "", user_id,
                     full_name or "Anonymous", username or "", message_text or "", action_context, datetime.now())
                )
                if watchlist_id:
                    self.cursor.execute(
                        'UPDATE surveillance_watchlists SET alert_count = alert_count + 1 WHERE id = ?',
                        (watchlist_id,)
                    )
                self.conn.commit()
            except Exception:
                pass

    async def get_surveillance_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch logged surveillance hits."""
        async with self.lock:
            self.cursor.execute(
                '''SELECT id, target_type, trigger_val, group_id, group_title, user_id, full_name, username, message_text, action_context, timestamp
                FROM surveillance_alerts ORDER BY timestamp DESC LIMIT ?''',
                (limit,)
            )
            rows = self.cursor.fetchall()
            return [{
                "id": r[0], "target_type": r[1], "trigger_val": r[2], "group_id": r[3],
                "group_title": r[4], "user_id": r[5], "full_name": r[6], "username": r[7],
                "message_text": r[8], "action_context": r[9], "timestamp": str(r[10])
            } for r in rows]

    # ═══════════════════════════════════════════════════════
    #   CROSS-GROUP OSINT & PERSONA TRACKING
    # ═══════════════════════════════════════════════════════
    async def update_user_cross_profile(self, user_id: int, full_name: str, username: str,
                                        group_id: int, group_title: str, 
                                        is_premium: bool = False, is_verified: bool = False, 
                                        phone: str = "", is_vc: bool = False):
        """Update persistent multi-group intelligence on any observed user."""
        if not user_id:
            return
        async with self.lock:
            try:
                self.cursor.execute(
                    'SELECT name_history, username_history, spotted_groups, message_count, vc_participations FROM user_cross_profiles WHERE user_id = ?',
                    (user_id,)
                )
                row = self.cursor.fetchone()
                now = datetime.now()

                if row:
                    names = json.loads(row[0] or '[]')
                    usernames = json.loads(row[1] or '[]')
                    groups = json.loads(row[2] or '[]')
                    msg_count = row[3] + (0 if is_vc else 1)
                    vc_count = row[4] + (1 if is_vc else 0)

                    if full_name and full_name not in names:
                        names.append(full_name)
                    if username and username not in usernames:
                        usernames.append(username)
                    
                    grp_entry = {"id": group_id, "title": group_title}
                    if not any(g.get("id") == group_id for g in groups if isinstance(g, dict)):
                        groups.append(grp_entry)

                    self.cursor.execute(
                        '''UPDATE user_cross_profiles
                        SET last_seen = ?, current_name = ?, current_username = ?,
                            name_history = ?, username_history = ?, spotted_groups = ?,
                            message_count = ?, vc_participations = ?,
                            is_premium = COALESCE(?, is_premium),
                            is_verified = COALESCE(?, is_verified),
                            phone = CASE WHEN ? != '' THEN ? ELSE phone END
                        WHERE user_id = ?''',
                        (now, full_name, username, json.dumps(names), json.dumps(usernames),
                         json.dumps(groups), msg_count, vc_count, is_premium, is_verified,
                         phone, phone, user_id)
                    )
                else:
                    names = [full_name] if full_name else []
                    usernames = [username] if username else []
                    groups = [{"id": group_id, "title": group_title}]
                    msg_count = 0 if is_vc else 1
                    vc_count = 1 if is_vc else 0

                    self.cursor.execute(
                        '''INSERT INTO user_cross_profiles
                        (user_id, first_seen, last_seen, current_name, current_username,
                         name_history, username_history, spotted_groups, message_count, vc_participations,
                         is_premium, is_verified, phone)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (user_id, now, now, full_name or "", username or "",
                         json.dumps(names), json.dumps(usernames), json.dumps(groups),
                         msg_count, vc_count, is_premium, is_verified, phone or "")
                    )
                self.conn.commit()
            except Exception:
                pass

    async def get_user_cross_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve full cross-group OSINT dossier for a target user."""
        async with self.lock:
            self.cursor.execute(
                '''SELECT user_id, first_seen, last_seen, current_name, current_username,
                          name_history, username_history, spotted_groups, message_count,
                          vc_participations, is_premium, is_verified, phone
                FROM user_cross_profiles WHERE user_id = ?''',
                (user_id,)
            )
            r = self.cursor.fetchone()
            if not r:
                return None
            return {
                "user_id": r[0],
                "first_seen": str(r[1]),
                "last_seen": str(r[2]),
                "current_name": r[3],
                "current_username": r[4],
                "name_history": json.loads(r[5] or '[]'),
                "username_history": json.loads(r[6] or '[]'),
                "spotted_groups": json.loads(r[7] or '[]'),
                "message_count": r[8],
                "vc_participations": r[9],
                "is_premium": bool(r[10]),
                "is_verified": bool(r[11]),
                "phone": r[12]
            }

    # ═══════════════════════════════════════════════════════
    #   VC SPEAKER TELEMETRY & TALKER LEADERBOARD
    # ═══════════════════════════════════════════════════════
    async def record_vc_speaker_activity(self, group_id: int, call_id: str, user_id: int,
                                         full_name: str, username: str, delta_sec: float = 2.0):
        """Accumulate live speech duration for VC participants."""
        if not user_id:
            return
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT INTO vc_talker_telemetry (group_id, call_id, user_id, full_name, username, total_spoke_sec, last_spoke_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id, call_id, user_id) DO UPDATE SET
                        total_spoke_sec = total_spoke_sec + ?,
                        full_name = ?,
                        username = ?,
                        last_spoke_at = ?''',
                    (group_id, str(call_id), user_id, full_name or "", username or "", delta_sec, datetime.now(),
                     delta_sec, full_name or "", username or "", datetime.now())
                )
                self.conn.commit()
            except Exception:
                pass

    async def get_vc_talker_leaderboard(self, group_id: int, call_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch cumulative talk-time leaderboard for active voice chamber."""
        async with self.lock:
            params = [group_id]
            query = "SELECT user_id, full_name, username, total_spoke_sec, last_spoke_at FROM vc_talker_telemetry WHERE group_id = ?"
            if call_id:
                query += " AND call_id = ?"
                params.append(str(call_id))
            query += " ORDER BY total_spoke_sec DESC LIMIT 20"
            
            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
            
            total_room_speech = sum(r[3] for r in rows) or 1.0
            return [{
                "user_id": r[0],
                "full_name": r[1],
                "username": r[2],
                "total_spoke_sec": round(r[3], 1),
                "share_pct": round((r[3] / total_room_speech) * 100, 1),
                "last_spoke_at": str(r[4])
            } for r in rows]

    # =========================================================================
    #  SIGNAL & IOC ENTITY RADAR
    # =========================================================================
    async def save_extracted_entities(self, entities: List[Dict[str, Any]]):
        if not entities:
            return
        async with self.lock:
            try:
                for e in entities:
                    self.cursor.execute(
                        '''INSERT INTO extracted_entities 
                        (group_id, group_title, message_id, sender_id, sender_name, entity_type, entity_value, context, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (e.get("group_id", 0), e.get("group_title", ""), e.get("message_id", 0),
                         e.get("sender_id", 0), e.get("sender_name", ""), e.get("entity_type", ""),
                         e.get("entity_value", ""), e.get("context", "")[:250],
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    )
                self.conn.commit()
            except Exception as err:
                logger.error(f"Error saving extracted entities: {err}")

    async def get_extracted_entities(self, group_id: Optional[int] = None, entity_type: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        async with self.lock:
            query = "SELECT id, group_id, group_title, message_id, sender_id, sender_name, entity_type, entity_value, context, timestamp FROM extracted_entities WHERE 1=1"
            params = []
            if group_id:
                query += " AND group_id = ?"
                params.append(group_id)
            if entity_type and entity_type != 'all':
                query += " AND entity_type = ?"
                params.append(entity_type)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
            items = [{
                "id": r[0],
                "group_id": r[1],
                "group_title": r[2],
                "message_id": r[3],
                "sender_id": r[4],
                "sender_name": r[5],
                "entity_type": r[6],
                "entity_value": r[7],
                "context": r[8],
                "timestamp": str(r[9])
            } for r in rows]

            # Aggregate stats across database
            stats_query = "SELECT entity_type, COUNT(*) FROM extracted_entities"
            stats_params = []
            if group_id:
                stats_query += " WHERE group_id = ?"
                stats_params.append(group_id)
            stats_query += " GROUP BY entity_type"
            self.cursor.execute(stats_query, stats_params)
            stats = {r[0]: r[1] for r in self.cursor.fetchall()}

            return {
                "items": items,
                "stats": stats,
                "total": sum(stats.values())
            }

    # =========================================================================
    #  SOCIAL INTERACTION & RELATIONSHIP NETWORK GRAPH
    # =========================================================================
    async def record_social_interaction(self, group_id: int, source_user_id: int, source_name: str, 
                                        target_user_id: int, target_name: str, interaction_type: str, message_id: int):
        if not source_user_id or not target_user_id or source_user_id == target_user_id:
            return
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT INTO interaction_network 
                    (group_id, source_user_id, source_name, target_user_id, target_name, interaction_type, message_id, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (group_id, source_user_id, source_name or f"User #{source_user_id}",
                     target_user_id, target_name or f"User #{target_user_id}",
                     interaction_type, message_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                self.conn.commit()
            except Exception as err:
                logger.error(f"Error recording social interaction: {err}")

    async def get_social_graph(self, group_id: Optional[int] = None, limit: int = 150) -> Dict[str, Any]:
        async with self.lock:
            query = '''
                SELECT source_user_id, source_name, target_user_id, target_name, COUNT(*) as weight
                FROM interaction_network
                WHERE 1=1
            '''
            params = []
            if group_id:
                query += " AND group_id = ?"
                params.append(group_id)
            query += " GROUP BY source_user_id, target_user_id ORDER BY weight DESC LIMIT ?"
            params.append(limit)

            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()

            nodes_dict = {}
            links = []

            for r in rows:
                s_id, s_name, t_id, t_name, w = r[0], r[1], r[2], r[3], r[4]
                if s_id not in nodes_dict:
                    nodes_dict[s_id] = {"id": s_id, "label": s_name, "degree": 0}
                if t_id not in nodes_dict:
                    nodes_dict[t_id] = {"id": t_id, "label": t_name, "degree": 0}

                nodes_dict[s_id]["degree"] += w
                nodes_dict[t_id]["degree"] += w

                links.append({
                    "source": s_id,
                    "target": t_id,
                    "weight": w
                })

            nodes_list = list(nodes_dict.values())
            nodes_list.sort(key=lambda n: n["degree"], reverse=True)

            return {
                "nodes": nodes_list,
                "links": links,
                "top_influencers": nodes_list[:8],
                "edge_count": len(links),
                "node_count": len(nodes_list)
            }

    # =========================================================================
    #  AUTONOMOUS SIGNAL SIPHON & FORWARDING MATRIX
    # =========================================================================
    async def add_siphon_rule(self, rule_name: str, match_type: str, match_value: str, destination: str = "me") -> Dict[str, Any]:
        async with self.lock:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.cursor.execute(
                '''INSERT INTO siphon_rules (rule_name, match_type, match_value, destination, is_active, siphoned_count, created_at)
                VALUES (?, ?, ?, ?, 1, 0, ?)''',
                (rule_name, match_type, match_value, destination, now)
            )
            self.conn.commit()
            return {
                "id": self.cursor.lastrowid,
                "rule_name": rule_name,
                "match_type": match_type,
                "match_value": match_value,
                "destination": destination,
                "is_active": True,
                "siphoned_count": 0,
                "created_at": now
            }

    async def get_siphon_rules(self) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute("SELECT id, rule_name, match_type, match_value, destination, is_active, siphoned_count, created_at FROM siphon_rules ORDER BY id DESC")
            return [{
                "id": r[0],
                "rule_name": r[1],
                "match_type": r[2],
                "match_value": r[3],
                "destination": r[4],
                "is_active": bool(r[5]),
                "siphoned_count": r[6],
                "created_at": str(r[7])
            } for r in self.cursor.fetchall()]

    async def delete_siphon_rule(self, rule_id: int) -> bool:
        async with self.lock:
            self.cursor.execute("DELETE FROM siphon_rules WHERE id = ?", (rule_id,))
            self.conn.commit()
            return self.cursor.rowcount > 0

    async def toggle_siphon_rule(self, rule_id: int, is_active: bool) -> bool:
        async with self.lock:
            self.cursor.execute("UPDATE siphon_rules SET is_active = ? WHERE id = ?", (1 if is_active else 0, rule_id))
            self.conn.commit()
            return self.cursor.rowcount > 0

    async def log_siphon_event(self, rule_id: int, rule_name: str, group_id: int, group_title: str, 
                               sender_name: str, matched_text: str, destination: str = "me"):
        async with self.lock:
            try:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.cursor.execute(
                    '''INSERT INTO siphon_logs (rule_id, rule_name, group_id, group_title, sender_name, matched_text, destination, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (rule_id, rule_name, group_id, group_title, sender_name, matched_text[:300], destination, now)
                )
                self.cursor.execute("UPDATE siphon_rules SET siphoned_count = siphoned_count + 1 WHERE id = ?", (rule_id,))
                self.conn.commit()
            except Exception as err:
                logger.error(f"Error logging siphon event: {err}")

    async def get_siphon_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self.lock:
            self.cursor.execute("SELECT id, rule_id, rule_name, group_id, group_title, sender_name, matched_text, destination, timestamp FROM siphon_logs ORDER BY id DESC LIMIT ?", (limit,))
            return [{
                "id": r[0],
                "rule_id": r[1],
                "rule_name": r[2],
                "group_id": r[3],
                "group_title": r[4],
                "sender_name": r[5],
                "matched_text": r[6],
                "destination": r[7],
                "timestamp": str(r[8])
            } for r in self.cursor.fetchall()]

    # =========================================================================
    #  GLOBAL FORENSIC FULL-TEXT SEARCH
    # =========================================================================
    async def global_forensic_search(self, query: str, group_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        async with self.lock:
            sql = '''
                SELECT id, group_id, group_title, message_id, user_id, full_name, username, 
                       text, is_deleted, is_edited, original_text, date_sent, date_deleted, date_edited
                FROM intercepted_messages
                WHERE (text LIKE ? OR original_text LIKE ? OR full_name LIKE ? OR username LIKE ?)
            '''
            q_like = f"%{query}%"
            params = [q_like, q_like, q_like, q_like]
            if group_id:
                sql += " AND group_id = ?"
                params.append(group_id)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            self.cursor.execute(sql, params)
            return [{
                "id": r[0],
                "group_id": r[1],
                "group_title": r[2],
                "message_id": r[3],
                "user_id": r[4],
                "full_name": r[5],
                "username": r[6],
                "text": r[7],
                "message_text": r[7],
                "is_deleted": bool(r[8]),
                "is_edited": bool(r[9]),
                "original_text": r[10],
                "date_sent": str(r[11]),
                "date_deleted": str(r[12]) if r[12] else "",
                "date_edited": str(r[13]) if r[13] else ""
            } for r in self.cursor.fetchall()]

    # =========================================================================
    #  MULTI-OPERATOR RBAC & CLEARANCE REGISTRY
    # =========================================================================
    async def get_operator_clearance(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch active or revoked operator clearance by normalized Telegram username."""
        clean_user = str(username or "").strip().lstrip('@').lower()
        if not clean_user:
            return None
        async with self.lock:
            self.cursor.execute(
                '''SELECT id, username, pin_hash, role, status, assigned_by, created_at, updated_at
                   FROM operator_clearances WHERE username = ?''',
                (clean_user,)
            )
            r = self.cursor.fetchone()
            if not r:
                return None
            return {
                "id": r[0],
                "username": r[1],
                "pin_hash": r[2],
                "role": r[3],
                "status": r[4],
                "assigned_by": r[5],
                "created_at": str(r[6]),
                "updated_at": str(r[7])
            }

    async def list_operator_clearances(self) -> List[Dict[str, Any]]:
        """List all registered operators with access levels and creation timestamps."""
        async with self.lock:
            self.cursor.execute(
                '''SELECT id, username, role, status, assigned_by, created_at, updated_at
                   FROM operator_clearances ORDER BY CASE WHEN role = 'owner' THEN 0 ELSE 1 END, id ASC'''
            )
            return [{
                "id": r[0],
                "username": r[1],
                "role": r[2],
                "status": r[3],
                "assigned_by": r[4],
                "created_at": str(r[5]),
                "updated_at": str(r[6])
            } for r in self.cursor.fetchall()]

    async def save_operator_clearance(self, username: str, pin_hash: str, role: str = 'operator', assigned_by: str = 'archfiend000') -> Dict[str, Any]:
        """Grant or update an operator clearance record."""
        clean_user = str(username or "").strip().lstrip('@').lower()
        async with self.lock:
            self.cursor.execute(
                '''INSERT INTO operator_clearances (username, pin_hash, role, status, assigned_by, updated_at)
                   VALUES (?, ?, ?, 'active', ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(username) DO UPDATE SET 
                       pin_hash = excluded.pin_hash,
                       role = excluded.role,
                       status = 'active',
                       assigned_by = excluded.assigned_by,
                       updated_at = CURRENT_TIMESTAMP''',
                (clean_user, pin_hash, role, assigned_by)
            )
            self.conn.commit()
            return {
                "username": clean_user,
                "role": role,
                "status": "active",
                "assigned_by": assigned_by
            }

    async def revoke_operator_clearance(self, username: str) -> bool:
        """Revoke clearance for a given operator (cannot revoke master owner)."""
        clean_user = str(username or "").strip().lstrip('@').lower()
        if clean_user == "archfiend000":
            return False
        async with self.lock:
            self.cursor.execute(
                '''UPDATE operator_clearances 
                   SET status = 'revoked', updated_at = CURRENT_TIMESTAMP
                   WHERE username = ? AND role != 'owner' ''',
                (clean_user,)
            )
            self.conn.commit()
            return self.cursor.rowcount > 0

    async def delete_operator_clearance(self, username: str) -> bool:
        """Permanently remove an operator clearance (cannot delete master owner)."""
        clean_user = str(username or "").strip().lstrip('@').lower()
        if clean_user == "archfiend000":
            return False
        async with self.lock:
            self.cursor.execute(
                '''DELETE FROM operator_clearances WHERE username = ? AND role != 'owner' ''',
                (clean_user,)
            )
            self.conn.commit()
            return self.cursor.rowcount > 0

    async def record_security_event(self, event_type: str, username: str = "", client_ip: str = "", detail: str = "", severity: str = "INFO"):
        """Record a structured security event into the persistent audit trail."""
        async with self.lock:
            try:
                self.cursor.execute(
                    '''INSERT INTO security_audit_logs (event_type, username, client_ip, detail, severity, timestamp)
                       VALUES (?, ?, ?, ?, ?, datetime('now'))''',
                    (event_type, username or "", client_ip or "", detail or "", severity)
                )
                self.conn.commit()
            except Exception as e:
                logger.debug(f"Failed to record security audit log: {e}")

    async def get_security_audit_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent security audit log records with strict limit clamping."""
        async with self.lock:
            try:
                clamped_limit = max(1, min(int(limit), 500))
                self.cursor.execute(
                    '''SELECT id, event_type, username, client_ip, detail, severity, timestamp 
                       FROM security_audit_logs 
                       ORDER BY id DESC LIMIT ?''',
                    (clamped_limit,)
                )
                rows = self.cursor.fetchall()
                return [
                    {
                        "id": r[0],
                        "event_type": r[1],
                        "username": r[2],
                        "client_ip": r[3],
                        "detail": r[4],
                        "severity": r[5],
                        "timestamp": r[6]
                    }
                    for r in rows
                ]
            except Exception as e:
                logger.error(f"Error fetching security audit logs: {e}")
                return []

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
