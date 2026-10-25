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
    def calculate_reputation_score(
        user_id: Any = 0,
        message_count: int = 0,
        days_active: float = 0.0,
        reactions_received: int = 0,
        replies_received: int = 0,
        media_count: int = 0,
        is_verified: bool = False,
        is_premium: bool = False,
        has_photo: bool = True,
        has_username: bool = True,
        is_bot: bool = False,
        is_deleted: bool = False,
        is_scam: bool = False,
        is_fake: bool = False,
        scam_score: int = 0
    ) -> Dict[str, Any]:
        """Multi-factor scientific reputation scoring calibrated for 100k message analysis."""
        if isinstance(user_id, dict):
            d = user_id
            user_id = int(d.get("user_id", 0) or 0)
            message_count = int(d.get("message_count", message_count))
            days_active = float(d.get("days_active", days_active))
            reactions_received = int(d.get("reactions_received", reactions_received))
            replies_received = int(d.get("replies_received", replies_received))
            media_count = int(d.get("media_count", media_count))
            is_verified = bool(d.get("is_verified", is_verified))
            is_premium = bool(d.get("is_premium", is_premium))
            has_photo = bool(d.get("has_photo", has_photo))
            has_username = bool(d.get("has_username", has_username))
            is_bot = bool(d.get("is_bot", is_bot))
            is_deleted = bool(d.get("is_deleted", is_deleted))
            is_scam = bool(d.get("is_scam", is_scam))
            is_fake = bool(d.get("is_fake", is_fake))
            scam_score = int(d.get("scam_score", scam_score))
            if "risk_flags" in d and isinstance(d["risk_flags"], list) and len(d["risk_flags"]) > 0:
                scam_score = max(scam_score, len(d["risk_flags"]) * 20)
        else:
            user_id = int(user_id or 0)

        if is_deleted or is_scam or is_fake:
            return {
                "score": 0,
                "tier": "CRITICAL RISK / SCAM",
                "color": "#ef4444",
                "factors": ["Official Telegram Scam / Fake / Deleted marker"]
            }

        score = 50
        factors = []

        # 1. Chat Volume Impact (from 100k history)
        if message_count >= 1000:
            score += 25
            factors.append(f"Chat Pillar ({message_count:,} msgs)")
        elif message_count >= 500:
            score += 20
            factors.append(f"Veteran Chatter ({message_count:,} msgs)")
        elif message_count >= 200:
            score += 16
            factors.append(f"Core Regular ({message_count} msgs)")
        elif message_count >= 50:
            score += 12
            factors.append(f"Active Contributor ({message_count} msgs)")
        elif message_count >= 10:
            score += 8
            factors.append(f"Casual Chatter ({message_count} msgs)")
        elif message_count >= 3:
            score += 4
            factors.append(f"New Participant ({message_count} msgs)")
        elif message_count == 0:
            score -= 10
            factors.append("Zero Chat History (Lurker / Inactive)")

        # 2. Longevity / Tenure in Chat
        if days_active >= 90:
            score += 15
            factors.append("Established Member (3+ Months Active)")
        elif days_active >= 30:
            score += 10
            factors.append("Multi-Week Regular (1+ Month Active)")
        elif days_active >= 7:
            score += 5
            factors.append("Recent Regular (1+ Week Active)")
        elif days_active >= 1:
            score += 2

        # 3. Social Validation (Reactions & Replies Received)
        if reactions_received >= 50:
            score += 10
            factors.append(f"High Community Approval ({reactions_received} reactions)")
        elif reactions_received >= 10:
            score += 5
            factors.append(f"Received Reactions ({reactions_received})")

        if replies_received >= 30:
            score += 8
            factors.append(f"Conversational Anchor ({replies_received} replies)")
        elif replies_received >= 5:
            score += 4

        # 4. Account Vintage & Credential Authority
        if is_verified:
            score += 20
            factors.append("Official Telegram Verified")
        if is_premium:
            score += 10
            factors.append("Telegram Premium Subscriber")
        if 0 < user_id < 1000000000:
            score += 10
            factors.append("OG Account (<1B)")
        elif 1000000000 <= user_id < 4500000000:
            score += 5
            factors.append("Established Account (<4.5B)")
        elif user_id > 6800000000:
            if message_count < 5:
                score -= 15
                factors.append("Unproven New ID (>6.8B)")
            else:
                score -= 5

        if has_photo:
            score += 5
        else:
            if message_count < 5:
                score -= 10
                factors.append("No Profile Photo")

        if has_username:
            score += 3

        if is_bot:
            score -= 25
            factors.append("Automated Bot")

        if scam_score > 0:
            deduction = int(scam_score * 0.7)
            score -= deduction
            if deduction > 10:
                factors.append(f"Forensic Risk Penalty (-{deduction} pts)")

        score = max(0, min(100, score))

        if score >= 90:
            tier = "LEGENDARY PILLAR"
            color = "#10b981" # Emerald Green
        elif score >= 80:
            tier = "SOVEREIGN VERIFIED"
            color = "#34d399" # Mint Green
        elif score >= 70:
            tier = "CORE CONTRIBUTOR"
            color = "#38bdf8" # Sky Blue
        elif score >= 55:
            tier = "REGULAR CITIZEN"
            color = "#818cf8" # Indigo
        elif score >= 40:
            tier = "CASUAL NEWCOMER"
            color = "#94a3b8" # Slate
        elif score >= 25:
            tier = "SUSPICIOUS TRANSIENT"
            color = "#f59e0b" # Amber
        else:
            tier = "CRITICAL RISK"
            color = "#ef4444" # Crimson

        return {
            "score": score,
            "tier": tier,
            "color": color,
            "factors": factors
        }

    @staticmethod
    def inspect_user(user, message_count: int = -1, member_stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Perform comprehensive forensic profiling of a Telegram member."""
        user_id = getattr(user, 'id', 0) if user else 0
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

        # Determine actual message count and tenure from member_stats or message_count
        effective_msg_count = message_count if message_count >= 0 else 0
        days_active = 0.0
        reactions_recv = 0
        replies_recv = 0
        media_cnt = 0

        if member_stats:
            effective_msg_count = member_stats.get("message_count", effective_msg_count)
            reactions_recv = member_stats.get("reactions_received", 0)
            replies_recv = member_stats.get("replies_received", 0)
            media_cnt = member_stats.get("media_count", 0)
            f_seen = member_stats.get("first_seen")
            l_seen = member_stats.get("last_seen")
            if f_seen and l_seen:
                try:
                    from datetime import datetime
                    if isinstance(f_seen, str): f_seen = datetime.fromisoformat(f_seen)
                    if isinstance(l_seen, str): l_seen = datetime.fromisoformat(l_seen)
                    days_active = max(0.0, (l_seen - f_seen).total_seconds() / 86400.0)
                except Exception:
                    days_active = 0.0

        # Calculate rich multi-factor reputation score
        rep = ForensicInspector.calculate_reputation_score(
            user_id=user_id,
            message_count=effective_msg_count,
            days_active=days_active,
            reactions_received=reactions_recv,
            replies_received=replies_recv,
            media_count=media_cnt,
            is_verified=is_verified,
            is_premium=is_premium,
            has_photo=has_photo,
            has_username=bool(username),
            is_bot=is_bot,
            is_deleted=is_deleted,
            is_scam=is_scam,
            is_fake=is_fake,
            scam_score=scam_score
        )
        trust_score = rep["score"]
        trust_level = rep["tier"]
        trust_color = rep["color"]
        reputation_factors = rep["factors"]

        vintage = "Recent (2024-2026)"
        if user_id < 1000000000:
            vintage = "OG Early Adopter (2014-2019)"
        elif user_id < 4500000000:
            vintage = "Established Veteran (2020-2023)"
        elif user_id > 6800000000:
            vintage = "New Throwaway (2025-2026)"

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
            "message_count": effective_msg_count,
            "trust_score": trust_score,
            "trust_level": trust_level,
            "trust_color": trust_color,
            "reputation_factors": reputation_factors,
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
