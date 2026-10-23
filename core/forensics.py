import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from telethon.tl.types import (
    UserStatusRecently, UserStatusLastWeek, 
    UserStatusLastMonth, UserStatusEmpty, UserStatusOffline
)

SCAM_KEYWORDS = [
    r'crypto', r'forex', r'invest', r'profit', r'binance', r'airdrop',
    r'claim', r'admin', r'support', r'official', r'giveaway', r'bonus',
    r'signals', r'vip', r'trader', r'pump', r'elon', r'earn'
]
SCAM_REGEX = re.compile('|'.join(SCAM_KEYWORDS), re.IGNORECASE)

# Detect script mismatch or unicode homoglyphs
CYRILLIC_OR_ARABIC = re.compile(r'[\u0400-\u04FF\u0600-\u06FF]')

class ForensicInspector:
    """Heuristic engine for scoring member spam risk, ghost status, and profile traits."""

    @staticmethod
    def inspect_user(user, message_count: int = -1) -> Dict[str, Any]:
        """Perform comprehensive forensic profiling of a Telegram member."""
        user_id = user.id
        first_name = getattr(user, 'first_name', '') or ''
        last_name = getattr(user, 'last_name', '') or ''
        full_name = f"{first_name} {last_name}".strip()
        username = getattr(user, 'username', '') or ''
        
        is_bot = bool(getattr(user, 'bot', False))
        is_deleted = bool(getattr(user, 'deleted', False))
        is_verified = bool(getattr(user, 'verified', False))
        is_scam = bool(getattr(user, 'scam', False))
        is_fake = bool(getattr(user, 'fake', False))
        is_premium = bool(getattr(user, 'premium', False))
        has_photo = bool(getattr(user, 'photo', None) is not None)
        
        # Last seen / activity evaluation
        status = getattr(user, 'status', None)
        last_seen_str, days_offline = ForensicInspector._parse_status(status)
        
        # Ghost Score Calculation (0 to 100)
        ghost_score = 0
        if is_deleted:
            ghost_score = 100
        else:
            if days_offline is not None:
                if days_offline >= 90:
                    ghost_score += 70
                elif days_offline >= 30:
                    ghost_score += 45
                elif days_offline >= 14:
                    ghost_score += 25
            elif last_seen_str == "Long time ago" or last_seen_str == "Unknown":
                ghost_score += 65
            elif last_seen_str == "Within last month":
                ghost_score += 30

            if not has_photo:
                ghost_score += 20
            if not username:
                ghost_score += 10
            if message_count == 0:
                ghost_score += 15

        ghost_score = min(100, ghost_score)

        # Scam / Spammer Risk Score (0 to 100)
        scam_score = 0
        reasons = []
        
        if is_scam or is_fake:
            scam_score = 100
            reasons.append("Flagged officially by Telegram as scam/fake")
            
        if not is_deleted:
            # Keyword triggers in name or username
            if SCAM_REGEX.search(full_name) or SCAM_REGEX.search(username):
                scam_score += 40
                reasons.append("High-risk commercial/crypto keywords in profile")
                
            # Mixed unicode or strange characters
            if CYRILLIC_OR_ARABIC.search(full_name):
                scam_score += 20
                reasons.append("Non-latin script / potential homoglyph match")
                
            # Throwaway recent ID heuristic (IDs > 6.5 Billion created recently in 2024-2026)
            if user_id > 6500000000 and not has_photo and not username:
                scam_score += 35
                reasons.append("High ID (>6.5B) throwaway profile with no photo or handle")

        scam_score = min(100, scam_score)
        is_ghost = ghost_score >= 60

        # Non-Admin Sovereign Trust & Reputation Score (0 to 100)
        trust_score = 65
        vintage = "Recent (2024-2026)"
        if user_id < 1000000000:
            trust_score += 20
            vintage = "OG Early Adopter (2014-2019)"
        elif user_id < 4500000000:
            trust_score += 10
            vintage = "Established Veteran (2020-2023)"
        elif user_id > 6800000000:
            trust_score -= 15
            vintage = "New Throwaway (2025-2026)"

        if is_verified:
            trust_score += 25
        if is_premium:
            trust_score += 15
        if has_photo:
            trust_score += 10
        if username:
            trust_score += 5
        if is_bot:
            trust_score -= 10

        if is_scam or is_fake or is_deleted:
            trust_score = 0
        else:
            trust_score -= int(scam_score * 0.8)

        trust_score = max(0, min(100, trust_score))
        if trust_score >= 85:
            trust_level = "SOVEREIGN VERIFIED"
            trust_color = "#34d399" # Emerald
        elif trust_score >= 60:
            trust_level = "NEUTRAL CITIZEN"
            trust_color = "#38bdf8" # Sky
        elif trust_score >= 35:
            trust_level = "SUSPICIOUS ENTITY"
            trust_color = "#f59e0b" # Amber
        else:
            trust_level = "CRITICAL THREAT"
            trust_color = "#e0231c" # Crimson

        return {
            "id": user_id,
            "username": username,
            "full_name": full_name,
            "is_bot": is_bot,
            "is_deleted": is_deleted,
            "is_verified": is_verified,
            "is_scam": is_scam,
            "is_fake": is_fake,
            "is_premium": is_premium,
            "has_photo": has_photo,
            "last_seen": last_seen_str,
            "days_offline": days_offline,
            "ghost_score": ghost_score,
            "is_ghost": is_ghost,
            "scam_score": scam_score,
            "is_suspicious": scam_score >= 40,
            "reasons": reasons,
            "message_count": message_count,
            "trust_score": trust_score,
            "trust_level": trust_level,
            "trust_color": trust_color,
            "vintage": vintage
        }

    @staticmethod
    def _parse_status(status) -> (str, Optional[int]):
        """Format last seen status and approximate days inactive."""
        if not status:
            return "Unknown", None
            
        if isinstance(status, UserStatusRecently):
            return "Recently (0-3d)", 1
        elif isinstance(status, UserStatusLastWeek):
            return "Within last week", 5
        elif isinstance(status, UserStatusLastMonth):
            return "Within last month", 20
        elif isinstance(status, UserStatusEmpty):
            return "Long time ago", 999
        elif isinstance(status, UserStatusOffline):
            if hasattr(status, 'was_online') and status.was_online:
                diff = datetime.now(timezone.utc) - status.was_online
                days = diff.days
                return f"{days}d ago", days
        return "Unknown", None

    @staticmethod
    def filter_members(inspected_list: List[Dict[str, Any]], filter_opts: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Filter a pre-inspected list against arbitrary criteria combinations."""
        res = []
        for p in inspected_list:
            if filter_opts.get('skip_bots', True) and p['is_bot']:
                continue
            if filter_opts.get('skip_verified', True) and p['is_verified']:
                continue
            if filter_opts.get('skip_premium', False) and p['is_premium']:
                continue
            if filter_opts.get('only_ghosts', False) and not p['is_ghost']:
                continue
            if filter_opts.get('only_deleted', False) and not p['is_deleted']:
                continue
            if filter_opts.get('only_suspicious', False) and not p['is_suspicious']:
                continue
            if filter_opts.get('only_no_photo', False) and p['has_photo']:
                continue
            if filter_opts.get('only_no_username', False) and p['username']:
                continue
            if filter_opts.get('min_days_offline'):
                min_days = filter_opts['min_days_offline']
                if p['days_offline'] is None or p['days_offline'] < min_days:
                    continue
            res.append(p)
        return res
