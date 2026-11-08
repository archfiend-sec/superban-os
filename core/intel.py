import re
import time
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional

CASHTAG_REGEX = re.compile(r'\$([A-Za-z0-9]{2,10})\b')
CRYPTO_ADDR_REGEX = re.compile(r'\b(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}|(?:EQ|UQ)[0-9a-zA-Z_-]{46})\b')
URL_REGEX = re.compile(r'https?:\/\/[^\s<>"]+')

BULLISH_WORDS = {"buy", "long", "moon", "pump", "gem", "bull", "bullish", "ath", "alpha", "accumulate", "breakout", "rally", "profit", "hold", "hodl", "launch", "based", "lfg"}
BEARISH_WORDS = {"dump", "sell", "short", "fud", "scam", "rug", "rekt", "crash", "bear", "bearish", "down", "drop", "loss", "exit", "dead", "liquidation", "fake"}

class IntelSynthesizer:
    """Non-Admin AI & Heuristic Intelligence Synthesizer for Telegram Groups."""

    @staticmethod
    def synthesize_chat_intel(messages: List[Dict[str, Any]], group_title: str = "", deep_scan_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not messages and not deep_scan_data:
            return {
                "group_title": group_title,
                "message_count": 0,
                "executive_summary": "Insufficient recent message activity in group to construct intelligence briefing.",
                "sentiment": {"bullish": 50, "bearish": 10, "neutral": 40, "mood": "NEUTRAL OBSERVATION"},
                "hot_cashtags": [],
                "key_narratives": [],
                "top_influencers": [],
                "extracted_signals": [],
                "threat_alerts": []
            }

        total_msgs = len(messages)
        sender_counts = Counter()
        sender_meta = {}
        cashtags = Counter()
        signals = set()
        threats = []

        bull_count = 0
        bear_count = 0
        word_freq = Counter()

        # Stopwords to filter out
        stopwords = {
            "the", "and", "to", "of", "a", "in", "is", "that", "for", "i", "you", "it",
            "not", "on", "with", "he", "as", "you", "do", "at", "this", "but", "his",
            "by", "from", "they", "we", "say", "her", "she", "or", "an", "will", "my",
            "one", "all", "would", "there", "their", "what", "so", "up", "out", "if",
            "about", "who", "get", "which", "go", "me", "when", "make", "can", "like",
            "time", "no", "just", "him", "know", "take", "people", "into", "year", "your",
            "good", "some", "could", "them", "see", "other", "than", "then", "now", "look",
            "only", "come", "its", "over", "think", "also", "back", "after", "use", "two",
            "how", "our", "work", "first", "well", "way", "even", "new", "want", "because",
            "any", "these", "give", "day", "most", "us", "are", "have", "has", "was", "been"
        }

        for m in messages:
            text = m.get("text", "") or ""
            uid = m.get("user_id") or 0
            uname = m.get("full_name") or "User"
            handle = m.get("username") or ""

            if uid:
                sender_counts[uid] += 1
                sender_meta[uid] = {
                    "name": uname,
                    "handle": handle,
                    "trust_score": m.get("trust_score", 70),
                    "trust_level": m.get("trust_level", "NEUTRAL CITIZEN")
                }

            # Extract cashtags
            for tag in CASHTAG_REGEX.findall(text):
                cashtags[tag.upper()] += 1

            # Extract crypto addresses
            for addr in CRYPTO_ADDR_REGEX.findall(text):
                signals.add(addr)

            # Check threats
            if m.get("is_threat"):
                threats.append({
                    "sender": uname,
                    "type": m.get("threat_type", "SUSPICIOUS"),
                    "reason": m.get("reason", ""),
                    "time": m.get("date", "")
                })

            # Word tokenization & sentiment
            tokens = re.findall(r'\b[a-zA-Z]{3,15}\b', text.lower())
            for t in tokens:
                if t in BULLISH_WORDS:
                    bull_count += 1
                elif t in BEARISH_WORDS:
                    bear_count += 1
                elif t not in stopwords:
                    word_freq[t] += 1

        # Sentiment math
        sent_total = (bull_count + bear_count) or 1
        bull_pct = int(round((bull_count / (sent_total + 5)) * 100))
        bear_pct = int(round((bear_count / (sent_total + 5)) * 100))
        neutral_pct = max(0, 100 - (bull_pct + bear_pct))

        if bull_pct > bear_pct + 20:
            mood = "EUPHORIC / STRONG BULLISH"
        elif bear_pct > bull_pct + 20:
            mood = "HIGH FUD / PANIC BEARISH"
        elif bull_pct > bear_pct:
            mood = "MODERATELY OPTIMISTIC"
        elif bear_pct > bull_pct:
            mood = "SKEPTICAL / CAUTIOUS"
        else:
            mood = "NEUTRAL / CONSENSUS ACCUMULATION"

        # Hot topics (top non-stopwords)
        hot_topics = [w for w, c in word_freq.most_common(8)]

        # Top influencers: if 100k scan data is provided, incorporate 100k ranked leaders!
        top_influencers = []
        if deep_scan_data and deep_scan_data.get("top_chatters"):
            total_100k_msgs = max(1, deep_scan_data.get("total_messages_indexed", total_msgs))
            for tc in deep_scan_data["top_chatters"][:6]:
                top_influencers.append({
                    "user_id": tc.get("user_id"),
                    "name": tc.get("full_name") or "User",
                    "handle": tc.get("username", ""),
                    "message_count": tc.get("message_count", 0),
                    "share_pct": int(round((tc.get("message_count", 0) / total_100k_msgs) * 100)),
                    "trust_score": tc.get("reputation_score", 70),
                    "trust_level": tc.get("reputation_tier", "NEUTRAL CITIZEN"),
                    "persona_archetype": tc.get("persona_archetype", "ACTIVE CITIZEN // 市民")
                })
        else:
            for uid, count in sender_counts.most_common(5):
                info = sender_meta.get(uid, {})
                top_influencers.append({
                    "user_id": uid,
                    "name": info.get("name", "Unknown"),
                    "handle": info.get("handle", ""),
                    "message_count": count,
                    "share_pct": int(round((count / max(1, total_msgs)) * 100)),
                    "trust_score": info.get("trust_score", 70),
                    "trust_level": info.get("trust_level", "NEUTRAL CITIZEN"),
                    "persona_archetype": "ACTIVE CITIZEN // 市民"
                })

        # Formulate executive summary
        summary_sentences = []
        if deep_scan_data and deep_scan_data.get("total_messages_indexed"):
            summary_sentences.append(
                f"Synthesized intelligence from comprehensive 100,000-message deep scan across {deep_scan_data.get('total_members_profiled', 0)} profiled members in {group_title or 'target group'} ({deep_scan_data.get('total_messages_indexed'):,} total indexed messages)."
            )
        else:
            summary_sentences.append(f"Wiretapped {total_msgs} recent messages from {group_title or 'target group'}.")

        if cashtags:
            top_tags = [f"${t} ({c}x)" for t, c in cashtags.most_common(3)]
            summary_sentences.append(f"Primary asset discussion focused on: {', '.join(top_tags)}.")
        if hot_topics:
            summary_sentences.append(f"Key conversational themes detected: {', '.join(hot_topics[:4])}.")
        summary_sentences.append(f"Prevailing market tone is {mood} ({bull_pct}% Bull / {bear_pct}% Bear).")
        if top_influencers:
            lead_speaker = top_influencers[0]
            summary_sentences.append(f"Conversation led predominantly by {lead_speaker['name']} ({lead_speaker.get('persona_archetype', 'Pillar')}) driving {lead_speaker['message_count']} messages.")
        if threats:
            summary_sentences.append(f"⚠️ Tactical Warning: Intercepted {len(threats)} potential exploit or phishing indicators.")

        display_msg_count = deep_scan_data.get("total_messages_indexed", total_msgs) if deep_scan_data else total_msgs

        return {
            "group_title": group_title,
            "message_count": display_msg_count,
            "executive_summary": " ".join(summary_sentences),
            "sentiment": {
                "bullish": bull_pct,
                "bearish": bear_pct,
                "neutral": neutral_pct,
                "mood": mood
            },
            "hot_cashtags": [{"tag": t, "count": c} for t, c in cashtags.most_common(6)],
            "key_narratives": hot_topics,
            "top_influencers": top_influencers,
            "extracted_signals": list(signals)[:15],
            "threat_alerts": threats,
            "is_100k_scan": bool(deep_scan_data and deep_scan_data.get("total_messages_indexed"))
        }
