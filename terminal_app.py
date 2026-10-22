import sys
import os
import asyncio
import time
from typing import List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.columns import Columns
from rich.text import Text
from rich.align import Align
from rich.live import Live

from core.config import Config, SESSION_FILE
from core.db import DatabaseManager
from core.engine import SuperBanEngine, OperationState
from core.forensics import ForensicInspector
from telethon import TelegramClient
from telethon.sessions import StringSession

console = Console()

class TerminalApp:
    def __init__(self, engine: SuperBanEngine):
        self.engine = engine

    def show_banner(self):
        console.clear()
        banner_text = Text.from_markup(
            "[bold red]╔═══════════════════════════════════════════════════════════════════════╗[/bold red]\n"
            "[bold red]║[/bold red]   [bold red]影の道[/bold red]  [bold white]AFTERLIGHT[/bold white] [bold red]//[/bold red] [bold white]SUPERBAN OS v5.0 APEX[/bold white]                         [bold red]║[/bold red]\n"
            "[bold red]║[/bold red]   [dim]Where stillness reveals the unseen • Sovereign Terminal Console[/dim]    [bold red]║[/bold red]\n"
            "[bold red]╚═══════════════════════════════════════════════════════════════════════╝[/bold red]"
        )
        console.print(Align.center(banner_text))

    async def run(self):
        self.show_banner()
        me = await self.engine.client.get_me()
        user_info = f"[bold red]● Operator:[/bold red] [bold white]{me.first_name}[/bold white] | Handle: [white]@{me.username or 'none'}[/white] | Phone: [dim]{me.phone}[/dim] | ID: [dim]{me.id}[/dim]"
        console.print(Panel(user_info, border_style="red", padding=(0, 2)))

        while True:
            console.print("\n" + "─"*72)
            console.print("[bold red]▌ // CHAPTER 00 · TACTICAL COMMAND ARSENAL[/bold red]")
            console.print("─"*72)

            menu = (
                "[bold red][01][/bold red] 🎯 [bold white]Tactical Mass Moderation[/bold white] [dim](Ban / Kick / Mute / Unban)[/dim]\n"
                "[bold red][02][/bold red] 🛡️  [bold white]Sentinel Autonomous Guardian[/bold white] [dim](Live Raid Shield & Auto-Bouncer)[/dim]\n"
                "[bold red][03][/bold red] 🔒 [bold red]Emergency Chat Lockdown / Unlock[/bold red] [dim](Instant Group Silence)[/dim]\n"
                "[bold red][04][/bold red] 💬 [bold white]Chatter Spy & Community Leaderboard[/bold white] [dim](Protect Active Talkers)[/dim]\n"
                "[bold red][05][/bold red] 👻 [bold white]Ghost Hunter[/bold white] [dim](One-Click Inactive/Dead Account Purge)[/dim]\n"
                "[bold red][06][/bold red] 🚨 [bold red]Scam Raider Annihilator[/bold red] [dim](Purge Crypto Spammers & Homoglyphs)[/dim]\n"
                "[bold red][07][/bold red] 🧹 [bold white]Service Message Sweeper[/bold white] [dim](1-Click Wipe of Joined/Left/Pinned Clutter)[/dim]\n"
                "[bold red][08][/bold red] 🗑️  [bold white]Deep Message Scrubber[/bold white] [dim](Purge Message History in 100-msg Batches)[/dim]\n"
                "[bold red][09][/bold red] 💎 [bold white]Lead Scraper & Exporter[/bold white] [dim](Extract Active Leads to CSV/JSON)[/dim]\n"
                "[bold red][10][/bold red] ⚠️  [bold white]Strike & Warning System[/bold white] [dim](3-Strikes Auto-Ban Engine)[/dim]\n"
                "[bold red][11][/bold red] 🛡️  [bold white]Immune Whitelist Manager[/bold white] [dim](Add/Remove Protected VIPs)[/dim]\n"
                "[bold red][12][/bold red] 📊 [bold white]Audit Telemetry & Intelligence[/bold white] [dim](Global Database Stats)[/dim]\n"
                "[bold red][13][/bold red] ⚡ [bold red]DEFCON Defense Protocol[/bold red] [dim](Level 1 Nuclear - Level 5 Peace)[/dim]\n"
                "[bold red][14][/bold red] 📢 [bold white]High-Velocity Drip Broadcast[/bold white] [dim](Safe Personalized Direct Outreach)[/dim]\n"
                "[bold red][15][/bold red] 🤖 [bold white]Neural Threat Forensics[/bold white] [dim](Inspect Drainers & Homoglyphs)[/dim]\n"
                "[bold red][16][/bold red] 🎙️  [bold red]Voice Chamber Apex[/bold red] [dim](VC Launch, Mute All, Silence, Terminate, Intel)[/dim]\n"
                "[bold red][00][/bold red] 🚪 [dim]Exit Console[/dim]"
            )
            console.print(Panel(menu, border_style="red", title="[bold white]AFTERLIGHT TACTICAL MATRIX[/bold white]"))

            choice = Prompt.ask("[bold red]▌ Select Directive[/bold red]", choices=["1", "01", "2", "02", "3", "03", "4", "04", "5", "05", "6", "06", "7", "07", "8", "08", "9", "09", "10", "11", "12", "13", "14", "15", "16", "0", "00", "exit", "quit", "q"], default="01")
            if choice.lower() in ["0", "00", "exit", "quit", "q"]:
                console.print("\n[yellow]Shutting down Terminal War Room. Goodbye![/yellow]")
                return

            choice = str(int(choice))

            try:
                if choice == "1":
                    await self.tactical_moderation_flow()
                elif choice == "2":
                    await self.sentinel_flow()
                elif choice == "3":
                    await self.lockdown_flow()
                elif choice == "4":
                    await self.chatter_spy_flow()
                elif choice == "5":
                    await self.ghost_hunter_flow()
                elif choice == "6":
                    await self.scam_annihilator_flow()
                elif choice == "7":
                    await self.service_sweeper_flow()
                elif choice == "8":
                    await self.message_scrubber_flow()
                elif choice == "9":
                    await self.lead_scraper_flow()
                elif choice == "10":
                    await self.warning_system_flow()
                elif choice == "11":
                    await self.whitelist_flow()
                elif choice == "12":
                    await self.export_stats_flow()
                elif choice == "13":
                    await self.defcon_flow()
                elif choice == "14":
                    await self.broadcast_flow()
                elif choice == "15":
                    await self.threat_radar_flow()
                elif choice == "16":
                    await self.voice_chamber_flow()
                elif choice == "0":
                    console.print("\n[yellow]Shutting down Terminal War Room. Goodbye![/yellow]")
                    return
            except KeyboardInterrupt:
                console.print("\n[yellow]Action cancelled by operator.[/yellow]")
            except Exception as e:
                console.print(f"[bold red]Execution Error:[/bold red] {e}")

    async def select_group(self):
        with console.status("[bold green]Loading dialogs from Telegram...[/bold green]"):
            groups = await self.engine.get_dialogs()

        if not groups:
            console.print("[red]❌ No accessible groups or channels found on this account.[/red]")
            return None

        table = Table(title="[bold cyan]TARGET GROUP RECONNAISSANCE[/bold cyan]", border_style="dim")
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("Title / Entity Name", style="white font-semibold")
        table.add_column("Entity ID", style="dim")
        table.add_column("Type", style="green")
        table.add_column("Members", justify="right", style="yellow")

        for idx, g in enumerate(groups[:30], 1):
            g_type = "Channel" if g["is_channel"] else "Supergroup"
            members = str(g["members_count"]) if g["members_count"] else "Unknown"
            table.add_row(str(idx), g["title"], str(g["id"]), g_type, members)

        console.print(table)


    async def sentinel_flow(self):
        group = await self.select_group()
        if not group: return

        group_id = group["id"]
        is_active = self.engine.sentinel.is_shield_active(group_id)

        console.print(Panel(
            f"[bold green]SENTINEL AUTONOMOUS GUARDIAN[/bold green]\n"
            f"Target: [bold white]{group['title']}[/bold white] (ID: {group_id})\n"
            f"Status: [{'bold green' if is_active else 'bold red'}]{'ACTIVE' if is_active else 'STANDBY'}[/]\n"
            f"[dim]Features: Auto-ban crypto/scam profiles on join, 4-second raid threshold auto-lockdown, and spam link deletion.[/dim]",
            border_style="green"
        ))

        if not is_active:
            if Confirm.ask("Engage Sentinel Shield on this group now?"):
                self.engine.sentinel.enable_shield(group_id)
                console.print(f"[bold green]✓ Sentinel Shield ENGAGED on {group['title']}![/bold green]")
                console.print("[dim]Monitoring real-time events. Press Ctrl+C anytime to return to menu.[/dim]\n")
                try:
                    while True:
                        await asyncio.sleep(2)
                except KeyboardInterrupt:
                    console.print("\n[yellow]Returned to main menu. Shield remains active in background.[/yellow]")
        else:
            if Confirm.ask("Disengage Sentinel Shield on this group?"):
                self.engine.sentinel.disable_shield(group_id)
                console.print(f"[yellow]Sentinel Shield disengaged on {group['title']}.[/yellow]")

    async def lockdown_flow(self):
        group = await self.select_group()
        if not group: return

        console.print(Panel(
            f"[bold red]EMERGENCY GROUP LOCKDOWN[/bold red]\n"
            f"Target: [bold white]{group['title']}[/bold white] (ID: {group['id']})\n"
            f"[dim]Total lockdown revokes sending messages and media for all standard members immediately.[/dim]",
            border_style="red"
        ))

        action = Prompt.ask("Choose action", choices=["lock", "unlock", "cancel"], default="lock")
        if action == "lock":
            if Confirm.ask(f"[bold red]ENGAGE EMERGENCY LOCKDOWN on {group['title']}?[/bold red]"):
                success = await self.engine.sentinel.lockdown_group(group["id"])
                if success:
                    console.print(f"[bold red]🔒 CHAT IS LOCKED DOWN! Standard members muted.[/bold red]")
                else:
                    console.print("[red]Lockdown failed. Ensure your account is an Admin with change_info rights.[/red]")
        elif action == "unlock":
            if Confirm.ask(f"Lift lockdown and restore messaging for {group['title']}?"):
                success = await self.engine.sentinel.unlock_group(group["id"])
                if success:
                    console.print(f"[bold green]🔓 LOCKDOWN LIFTED! Chat restored to normal.[/bold green]")

    async def chatter_spy_flow(self):
        group = await self.select_group()
        if not group: return

        limit = IntPrompt.ask("Messages to analyze in chat history", default=1500)
        with console.status(f"[bold cyan]Scanning last {limit} messages for community activity...[/bold cyan]"):
            data = await self.engine.sentinel.analyze_chat_activity(group["entity"], limit=limit)

        table = Table(title=f"COMMUNITY ACTIVITY LEADERBOARD // {group['title']}", border_style="sky_blue1")
        table.add_column("Rank", style="cyan", justify="right")
        table.add_column("Chatter", style="white font-semibold")
        table.add_column("Handle", style="magenta")
        table.add_column("User ID", style="dim")
        table.add_column("Messages", justify="right", style="yellow")
        table.add_column("Chat Share", justify="right", style="green")

        for idx, c in enumerate(data["leaderboard"][:15], 1):
            table.add_row(
                str(idx), c["name"], f"@{c['username']}" if c['username'] else "--",
                str(c["user_id"]), str(c["message_count"]), f"{c['share_pct']}%"
            )

        console.print(table)

    async def tactical_moderation_flow(self):
        group = await self.select_group()
        if not group: return

        console.print(Panel(f"[bold white]Target:[/bold white] {group['title']} (ID: {group['id']})", border_style="green"))

        action = Prompt.ask("Choose moderation action", choices=["ban", "kick", "mute", "unban"], default="ban")
        
        # Duration selection
        duration_choice = Prompt.ask(
            "Select action duration",
            choices=["permanent", "1h", "24h", "7d", "30d"],
            default="permanent"
        )
        duration_map = {"permanent": 0, "1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
        duration_seconds = duration_map.get(duration_choice, 0)

        # Granular mute subtype
        permission_subtype = "full"
        if action == "mute":
            permission_subtype = Prompt.ask(
                "Select mute restriction scope",
                choices=["mute_all", "mute_links", "mute_media"],
                default="mute_all"
            )

        mode = Prompt.ask("Choose velocity profile", choices=["stealth", "normal", "fast", "turbo", "nuclear"], default="normal")
        purge = Confirm.ask("Purge historical messages sent by targeted users?", default=False)
        skip_admins = Confirm.ask("Protect group administrators & whitelisted accounts?", default=True)
        limit = IntPrompt.ask("Participant scan limit", default=3000)

        with console.status("[bold green]Scanning group participants...[/bold green]"):
            members = await self.engine.inspect_group_members(group["entity"], limit=limit)

        console.print(f"✓ Scanned [bold green]{len(members)}[/bold green] participants.")
        if not Confirm.ask(f"Launch [bold red]{action.upper()}[/bold red] on {len(members)} accounts in [cyan]{mode.upper()}[/cyan] mode?"):
            console.print("[yellow]Operation aborted by user.[/yellow]")
            return

        await self.run_tracker(
            group["entity"], members, 
            action=action, mode=mode, purge=purge, skip_admins=skip_admins,
            duration_seconds=duration_seconds, permission_subtype=permission_subtype
        )

    async def ghost_hunter_flow(self):
        group = await self.select_group()
        if not group: return

        days = IntPrompt.ask("Threshold: minimum days offline to classify as Ghost", default=30)
        with console.status("[bold purple]Hunting ghost & abandoned accounts...[/bold purple]"):
            members = await self.engine.inspect_group_members(group["entity"], limit=4000)

        ghosts = [m for m in members if (m['days_offline'] and m['days_offline'] >= days) or m['ghost_score'] >= 60]
        
        console.print(Panel(
            f"[bold purple]👻 GHOST ACCOUNTS IDENTIFIED:[/bold purple] [bold white]{len(ghosts)}[/bold white] / {len(members)} members\n"
            f"[dim]Criteria: Offline for ≥ {days} days, missing avatars, or deleted accounts.[/dim]",
            border_style="purple"
        ))

        if not ghosts:
            console.print("[green]No ghost accounts detected matching these parameters![/green]")
            return

        action = Prompt.ask("Action to perform on ghosts", choices=["ban", "kick", "mute"], default="ban")
        if Confirm.ask(f"Confirm execution of [bold red]{action.upper()}[/bold red] on all {len(ghosts)} ghost accounts?"):
            await self.run_tracker(group["entity"], ghosts, action=action, mode="normal", purge=False, skip_admins=True)

    async def scam_annihilator_flow(self):
        group = await self.select_group()
        if not group: return

        with console.status("[bold red]Executing forensic scan for spam/crypto accounts...[/bold red]"):
            members = await self.engine.inspect_group_members(group["entity"], limit=4000)

        scammers = [m for m in members if m['is_suspicious']]
        
        console.print(Panel(
            f"[bold red]🚨 SUSPICIOUS / SPAM ACCOUNTS IDENTIFIED:[/bold red] [bold white]{len(scammers)}[/bold white] accounts\n"
            f"[dim]Criteria: Crypto/scam keywords in profile, non-latin homoglyphs, or throwaway accounts.[/dim]",
            border_style="red"
        ))

        if not scammers:
            console.print("[green]✓ Clean group! Zero suspicious spam profiles found.[/green]")
            return

        purge = Confirm.ask("Also delete all recent spam messages sent by these accounts?", default=True)
        if Confirm.ask(f"Execute immediate purge of all {len(scammers)} suspicious accounts?"):
            await self.run_tracker(group["entity"], scammers, action="ban", mode="fast", purge=purge, skip_admins=True)

    async def message_scrubber_flow(self):
        group = await self.select_group()
        if not group: return

        days = IntPrompt.ask("Purge messages from the past how many days", default=7)
        if not Confirm.ask(f"[bold red]WARNING: Permanently wipe all messages from the last {days} days?[/bold red]"):
            return

        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=days)
        batch = []
        total_deleted = 0

        with console.status("[bold red]Purging messages in 100-item batch bursts...[/bold red]"):
            async for msg in self.engine.client.iter_messages(group["entity"], offset_date=cutoff):
                batch.append(msg.id)
                if len(batch) >= 100:
                    await self.engine.client.delete_messages(group["entity"], batch)
                    total_deleted += len(batch)
                    batch.clear()
                    await asyncio.sleep(0.8)

            if batch:
                await self.engine.client.delete_messages(group["entity"], batch)
                total_deleted += len(batch)

        console.print(f"[bold green]✓ History Scrubber complete! Purged {total_deleted} messages.[/bold green]")

    async def whitelist_flow(self):
        wl = await self.engine.db.get_whitelist()
        console.print(Panel(f"Current Protected Whitelist contains [bold cyan]{len(wl)}[/bold cyan] immune user IDs.", title="Whitelist Manager"))

        action = Prompt.ask("Action", choices=["list", "add", "remove", "back"], default="list")
        if action == "list":
            table = Table(title="Immune Whitelist", border_style="dim")
            table.add_column("User ID", style="cyan")
            for uid in wl:
                table.add_row(str(uid))
            console.print(table)

    async def export_stats_flow(self):
        stats = await self.engine.db.get_stats()
        console.print(Panel(
            f"[bold cyan]Total Actions Logged:[/bold cyan] {stats['total_actions']}\n"
            f"[bold green]Successful Actions:[/bold green]   {stats['successful_actions']}\n"
            f"[bold red]Failed Actions:[/bold red]       {stats['failed_actions']}\n"
            f"[bold yellow]Completed Operations:[/bold yellow] {stats['completed_sessions']}\n"
            f"[bold magenta]Protected Whitelist:[/bold magenta]  {stats['whitelisted_users']}",
            title="Global Database Audit Stats", border_style="cyan"
        ))

    async def service_sweeper_flow(self):
        group = await self.select_group()
        if not group: return

        console.print(Panel(
            f"[bold yellow]SERVICE MESSAGE SWEEPER (CLUTTER PURGE)[/bold yellow]\n"
            f"Target: [bold white]{group['title']}[/bold white] (ID: {group['id']})\n"
            f"[dim]Purges all cluttering service events: 'X joined', 'Y left', 'pinned message', and group photo updates.[/dim]",
            border_style="yellow"
        ))

        limit = IntPrompt.ask("Service scan depth (messages to inspect)", default=2500)
        if not Confirm.ask(f"Purge all service clutter from the last {limit} messages in {group['title']}?"):
            return

        with console.status("[bold yellow]Sweeping service messages in 100-item bursts...[/bold yellow]"):
            purged = await self.engine.clean_service_messages(group["entity"], limit=limit)

        console.print(f"[bold green]✓ Service Message Sweeper complete! Purged {purged} clutter events.[/bold green]")

    async def lead_scraper_flow(self):
        group = await self.select_group()
        if not group: return

        console.print(Panel(
            f"[bold bright_cyan]💎 HIGH-VALUE LEAD SCRAPER & PROFILER[/bold bright_cyan]\n"
            f"Target: [bold white]{group['title']}[/bold white] (ID: {group['id']})\n"
            f"[dim]Extracts real human members with recent activity, usernames, and Premium badges while filtering out bots and ghost accounts.[/dim]",
            border_style="bright_cyan"
        ))

        active_only = Confirm.ask("Filter strictly for active users only (ignore dead/ghost/bots)?", default=True)
        limit = IntPrompt.ask("Maximum participants to scan", default=3000)

        with console.status("[bold cyan]Extracting and profiling leads...[/bold cyan]"):
            leads = await self.engine.scrape_leads(group["entity"], active_only=active_only, limit=limit)

        console.print(f"[bold green]✓ Extracted {len(leads)} high-value leads from {group['title']}![/bold green]")

        table = Table(title=f"EXTRACTED LEADS PREVIEW // {group['title']}", border_style="cyan")
        table.add_column("#", justify="right", style="cyan")
        table.add_column("Full Name", style="white font-semibold")
        table.add_column("Handle", style="magenta")
        table.add_column("User ID", style="dim")
        table.add_column("Last Seen", style="green")
        table.add_column("Premium", justify="center")

        for idx, l in enumerate(leads[:20], 1):
            prem = "⭐" if l.get("is_premium") else "--"
            handle = f"@{l['username']}" if l.get("username") else "--"
            table.add_row(str(idx), l.get("full_name", "") or "--", handle, str(l["id"]), l.get("last_seen", "") or "--", prem)

        console.print(table)

    async def warning_system_flow(self):
        group = await self.select_group()
        if not group: return

        console.print(Panel(
            f"[bold orange1]⚠️ STRIKE & WARNING ENFORCEMENT ENGINE[/bold orange1]\n"
            f"Target: [bold white]{group['title']}[/bold white] (ID: {group['id']})\n"
            f"[dim]Policy: 3 strikes triggers an automatic ban and chat message purge.[/dim]",
            border_style="orange1"
        ))

        sub = Prompt.ask("Action", choices=["list", "warn", "clear", "back"], default="list")
        if sub == "list":
            warnings = await self.engine.db.get_warnings(group["id"])
            if not warnings:
                console.print("[green]✓ Clean group! Zero active strikes recorded.[/green]")
                return
            table = Table(title=f"ACTIVE STRIKES // {group['title']}", border_style="orange1")
            table.add_column("User ID", style="cyan")
            table.add_column("Strikes", justify="center", style="bold red")
            table.add_column("Reason", style="white")
            table.add_column("Last Warned", style="dim")
            for w in warnings:
                table.add_row(str(w["user_id"]), f"{w['strikes']}/3", w["reason"], str(w["last_warned_at"]))
            console.print(table)

    async def run_tracker(self, group_entity, targets: List[Dict[str, Any]], action: str, mode: str, purge: bool, skip_admins: bool, duration_seconds: int = 0, permission_subtype: str = "full"):
        total = len(targets)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(complete_style="cyan", finished_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("[green]✅ {task.fields[success]}[/green]"),
            TextColumn("[red]❌ {task.fields[failed]}[/red]"),
            TextColumn("•"),
            TextColumn("[yellow]⚡ {task.fields[speed]} u/s[/yellow]"),
            TimeRemainingColumn()
        ) as progress:
            task_id = progress.add_task(f"Executing {action.upper()}", total=total, success=0, failed=0, speed=0.0)

            def telemetry_listener(data):
                t = data.get("telemetry", {})
                progress.update(
                    task_id, 
                    completed=t.get("processed", 0),
                    success=t.get("success", 0),
                    failed=t.get("failed", 0),
                    speed=t.get("speed", 0.0)
                )

            self.engine.register_listener(telemetry_listener)
            await self.engine.run_operation(
                group_entity, targets, 
                action=action, mode=mode, 
                purge_messages=purge, skip_admins=skip_admins,
                duration_seconds=duration_seconds, permission_subtype=permission_subtype
            )
            progress.update(task_id, completed=total)

        console.print("[bold green]✓ Tactical operation completed successfully![/bold green]\n")

    async def defcon_flow(self):
        group = await self.select_group()
        if not group: return
        group_id = group["id"]
        current = await self.engine.sentinel.get_defcon_level(group_id)

        console.print(Panel(
            f"[bold red]⚡ DEFCON DEFENSE STATE MACHINE[/bold red]\n"
            f"Target: [bold white]{group['title']}[/bold white] (ID: {group_id})\n"
            f"Current Posture: [bold red]DEFCON {current['level']}[/bold red]\n\n"
            f"[bold red][1][/bold red] 🚨 DEFCON 1 - NUCLEAR LOCKDOWN (Mute all non-admins & ban active raiders)\n"
            f"[bold yellow][2][/bold yellow] ⚠️  DEFCON 2 - RAID IMMINENT (Auto-kick link spammers, 3s raid bouncer)\n"
            f"[bold blue][3][/bold blue] 🛡️  DEFCON 3 - ELEVATED GUARD (Auto-delete new member media & links)\n"
            f"[bold cyan][4][/bold cyan] 👁️  DEFCON 4 - GUARDED RECON (Purge join/leave clutter & silent audit)\n"
            f"[bold green][5][/bold green] 🕊️  DEFCON 5 - PEACE & STILLNESS (Standard monitoring, zero restrictions)",
            border_style="red"
        ))

        level_choice = IntPrompt.ask("Select new DEFCON posture (1-5)", default=current['level'])
        if 1 <= level_choice <= 5:
            await self.engine.sentinel.set_defcon_level(group_id, level_choice)
            console.print(f"[bold green]✓ DEFCON {level_choice} posture successfully engaged on {group['title']}![/bold green]\n")
        else:
            console.print("[red]Invalid DEFCON level.[/red]")

    async def broadcast_flow(self):
        group = await self.select_group()
        if not group: return
        group_id = group["id"]

        with console.status("[bold cyan]Retrieving active verified leads for broadcast...[/bold cyan]"):
            leads = await self.engine.db.get_leads(group_id)
            if not leads:
                leads = await self.engine.scrape_leads(group["entity"], active_only=True, limit=500)

        if not leads:
            console.print("[red]❌ No leads found for this group. Run Lead Scraper [09] first.[/red]")
            return

        console.print(f"[bold green]Targeting {len(leads)} verified group leads for direct drip outreach.[/bold green]")
        template = Prompt.ask(
            "Enter broadcast message template (Supports {first_name}, {username})",
            default="Hello {first_name}! We have an exclusive community update for active members."
        )

        if Confirm.ask(f"Ready to initiate safe drip broadcast to {len(leads)} members?"):
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(complete_style="green"),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console
            ) as progress:
                task_id = progress.add_task("[bold cyan]Broadcasting Drip Campaign...[/bold cyan]", total=len(leads))
                
                # Run broadcast
                res = await self.engine.broadcast_campaign(leads, template)
                progress.update(task_id, completed=len(leads))

            console.print(f"[bold green]✓ Broadcast completed: {res['sent']} sent, {res['failed']} failed.[/bold green]\n")

    async def threat_radar_flow(self):
        group = await self.select_group()
        if not group: return
        group_id = group["id"]

        with console.status("[bold red]Loading neural threat telemetry...[/bold red]"):
            threats = await self.engine.get_ai_threats(group_id, limit=25)

        if not threats:
            console.print("[green]✓ Clean Radar: Zero active threats or crypto drainers detected in this group.[/green]")
            return

        table = Table(title=f"[bold red]NEURAL THREAT LOGS: {group['title']}[/bold red]", border_style="red")
        table.add_column("Time", style="dim")
        table.add_column("Threat Classification", style="bold red")
        table.add_column("User ID", style="yellow")
        table.add_column("Confidence", justify="right", style="cyan")
        table.add_column("Reason / Pattern", style="white")
        table.add_column("Action Taken", style="bold green")

        for t in threats:
            table.add_row(
                t["timestamp"][-8:],
                t["threat_type"],
                str(t["user_id"]),
                f"{t['confidence']}%",
                t["reason"][:45],
                t["action_taken"]
            )

        console.print(table)

    async def voice_chamber_flow(self):
        group = await self.select_group()
        if not group: return
        group_entity = group["entity"]
        group_title = group["title"]
        group_id = group["id"]

        while True:
            is_connected = self.engine.is_in_voice_call(group_id)
            conn_status = "[bold green]● CONNECTED TO VC STREAM (GHOST LISTENER)[/bold green]" if is_connected else "[dim]DISCONNECTED (REMOTE RADAR)[/dim]"

            with console.status(f"[bold cyan]Scanning Voice Chat frequency for {group_title}...[/bold cyan]"):
                vc_info = await self.engine.get_group_call_info(group_entity)

            console.print("\n" + "═"*72)
            console.print(f"[bold red]🎙️ // TACTICAL VOICE CHAMBER APEX · {group_title}[/bold red]")
            console.print(f"Connection Status: {conn_status}")
            console.print("═"*72)

            is_live = vc_info.get("has_call", False)
            participants = vc_info.get("participants", []) if is_live else []

            if is_live:
                status_badge = "[bold green]● LIVE ACTIVE[/bold green]"
                title_display = f"[bold white]{vc_info.get('title')}[/bold white]"
                parts_count = vc_info.get("participants_count", len(participants))
                join_muted = "[bold red]LOCKED (Muted)[/bold red]" if vc_info.get("join_muted") else "[green]OPEN[/green]"

                console.print(f"Status: {status_badge} | Title: {title_display} | Participants: [cyan]{parts_count}[/cyan] | Entrance Policy: {join_muted}")

                if participants:
                    table = Table(border_style="red", title=f"[bold white]LIVE VOICE PARTICIPANTS ({len(participants)} LOADED)[/bold white]")
                    table.add_column("User ID", style="yellow")
                    table.add_column("Participant Dossier", style="bold white")
                    table.add_column("Handle", style="cyan")
                    table.add_column("Phone", style="dim")
                    table.add_column("State", style="bold")
                    table.add_column("Volume", justify="right")
                    table.add_column("Media / Hand", justify="center")

                    for p in participants:
                        badges = []
                        if p.get("is_admin"): badges.append("👑")
                        if p.get("is_premium"): badges.append("💎")
                        if p.get("is_bot"): badges.append("🤖")
                        if p.get("is_verified"): badges.append("✓")
                        badge_str = " ".join(badges) + " " if badges else ""
                        is_self = " [dim](YOU)[/dim]" if p["is_self"] else ""

                        state_str = "[bold red]MUTED[/bold red]" if p["muted"] else "[bold green]SPEAKING[/bold green]"
                        
                        media_tags = []
                        if p.get("video_joined"): media_tags.append("📹 CAM")
                        if p.get("is_screen_sharing"): media_tags.append("🖥️ SCREEN")
                        if p.get("raise_hand"): media_tags.append("✋ RAISED")
                        media_str = ", ".join(media_tags) if media_tags else "—"

                        table.add_row(
                            str(p["user_id"]),
                            f"{badge_str}{p['full_name']}{is_self}",
                            f"@{p['username']}" if p["username"] else "—",
                            p.get("phone") or "—",
                            state_str,
                            f"{p['volume']}%",
                            media_str
                        )
                    console.print(table)
            else:
                console.print("[dim yellow]No active Voice Chat currently running in this group.[/dim yellow]")

            console.print("\n[bold red]Directives:[/bold red]")
            if not is_live:
                console.print(" [1] 🎙️  [bold green]Launch New Voice Chamber[/bold green] (Start Group Call)")
                console.print(" [R] 🔄  Refresh Status")
                console.print(" [B] 🔙  Back to Main Arsenal")
                sub_choice = Prompt.ask("[bold red]Select Action[/bold red]", choices=["1", "r", "R", "b", "B"], default="1")
                if sub_choice.lower() == "b":
                    return
                elif sub_choice.lower() == "r":
                    continue
                elif sub_choice == "1":
                    vc_title = Prompt.ask("Voice Chamber Title", default=f"{group_title} Briefing")
                    with console.status("[bold green]Launching voice chamber...[/bold green]"):
                        await self.engine.create_group_call(group_entity, title=vc_title)
                    console.print(f"[bold green]✓ Voice chamber \"{vc_title}\" successfully launched![/bold green]")
                    await asyncio.sleep(1.5)
            else:
                if not is_connected:
                    console.print(" [J] 🎧 [bold green]Join VC (Ghost Listener Mode)[/bold green]")
                else:
                    console.print(" [L] 🔌 [bold yellow]Leave / Disconnect VC[/bold yellow]")

                console.print(" [A] 🔊 [bold green]Live Audio Listening Bridge (Hear VC in Speakers / Browser)[/bold green]")
                console.print(" [1] 🛑 [bold red]Kill / Terminate Voice Chamber[/bold red] (End Call)")
                console.print(" [2] 🤐 [bold yellow]Nuclear Silence (Mute All Participants)[/bold yellow]")
                console.print(" [3] 🔒 [bold cyan]Toggle Auto-Mute Entrance Policy[/bold cyan]")
                console.print(" [4] 🔇 [bold white]Mute Specific Participant[/bold white]")
                console.print(" [5] 🔊 [bold white]Unmute Specific Participant[/bold white]")
                console.print(" [6] 🔗 [bold magenta]Generate Direct VC Invite Link[/bold magenta]")
                console.print(" [7] ✏️  [bold white]Edit Voice Chamber Title[/bold white]")
                console.print(" [8] 👤 [bold cyan]Inspect Deep Forensic Participant Dossier[/bold cyan]")
                console.print(" [9] 📥 [bold green]Export All VC Members to JSON File[/bold green]")
                console.print(" [R] 🔄 Refresh Radar")
                console.print(" [B] 🔙 Back to Main Arsenal")

                valid_choices = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "A", "j", "J", "l", "L", "r", "R", "b", "B"]
                sub_choice = Prompt.ask("[bold red]Select Action[/bold red]", choices=valid_choices, default="r")
                
                if sub_choice.lower() == "b":
                    return
                elif sub_choice.lower() == "r":
                    continue
                elif sub_choice.lower() == "a":
                    import webbrowser
                    console.print("[bold green]🔊 Launching Web Mission Control Voice Chamber Audio Suite at http://127.0.0.1:8000 ...[/bold green]")
                    console.print("[dim]Click 'Hear VC (Live Audio)' in the dashboard to stream the call directly through your Mac speakers.[/dim]")
                    webbrowser.open("http://127.0.0.1:8000")
                    await asyncio.sleep(1.5)
                elif sub_choice.lower() == "j":
                    with console.status("[bold green]Connecting to voice chamber stream...[/bold green]"):
                        res = await self.engine.join_voice_call(group_entity, muted=True)
                    console.print(f"[bold green]✓ Successfully joined Voice Chamber as Ghost Listener (SSRC: {res.get('ssrc')})[/bold green]")
                    await asyncio.sleep(1.5)
                elif sub_choice.lower() == "l":
                    with console.status("[bold yellow]Leaving voice chamber stream...[/bold yellow]"):
                        await self.engine.leave_voice_call()
                    console.print("[bold yellow]✓ Disconnected from Voice Chamber.[/bold yellow]")
                    await asyncio.sleep(1.2)
                elif sub_choice == "1":
                    if Confirm.ask("[bold red]Terminate the active voice chat for everyone?[/bold red]"):
                        with console.status("[bold red]Ending voice chamber...[/bold red]"):
                            await self.engine.discard_group_call(group_entity)
                        console.print("[bold red]🛑 Voice chamber terminated.[/bold red]")
                        await asyncio.sleep(1.2)
                elif sub_choice == "2":
                    with console.status("[bold yellow]Silencing all participants...[/bold yellow]"):
                        res = await self.engine.mute_all_vc_participants(group_entity)
                    console.print(f"[bold yellow]✓ Silenced {res['muted_count']} participants. Entrance locked to muted.[/bold yellow]")
                    await asyncio.sleep(1.5)
                elif sub_choice == "3":
                    new_val = not vc_info.get("join_muted", False)
                    await self.engine.toggle_vc_join_muted(group_entity, new_val)
                    console.print(f"[bold cyan]✓ Auto-mute entrance policy set to: {new_val}[/bold cyan]")
                    await asyncio.sleep(1.2)
                elif sub_choice in ["4", "5"]:
                    target_uid = IntPrompt.ask("Enter Participant User ID")
                    should_mute = (sub_choice == "4")
                    await self.engine.edit_vc_participant(group_entity, target_uid, muted=should_mute)
                    console.print(f"[bold green]✓ User #{target_uid} {'muted' if should_mute else 'unmuted'}.[/bold green]")
                    await asyncio.sleep(1.2)
                elif sub_choice == "6":
                    inv = await self.engine.export_vc_invite(group_entity)
                    console.print(f"\n[bold green]🔗 Direct Voice Chat Link:[/bold green] [bold white]{inv.get('link')}[/bold white]\n")
                    Prompt.ask("Press Enter to continue")
                elif sub_choice == "7":
                    new_title = Prompt.ask("Enter new title")
                    await self.engine.edit_vc_title(group_entity, new_title)
                    console.print(f"[bold green]✓ VC title updated to \"{new_title}\".[/bold green]")
                    await asyncio.sleep(1.2)
                elif sub_choice == "8":
                    target_uid = IntPrompt.ask("Enter Participant User ID to inspect")
                    match = next((p for p in participants if p["user_id"] == target_uid), None)
                    if match:
                        dossier_text = (
                            f"[bold red]USER ID:[/bold red]         {match['user_id']}\n"
                            f"[bold red]FULL NAME:[/bold red]       {match['full_name']}\n"
                            f"[bold red]HANDLE:[/bold red]          @{match['username'] or 'None'}\n"
                            f"[bold red]PHONE:[/bold red]           {match.get('phone') or 'Protected'}\n"
                            f"[bold red]PREMIUM:[/bold red]         {'💎 YES' if match.get('is_premium') else 'NO'}\n"
                            f"[bold red]BOT / VERIFIED:[/bold red]  {'🤖 BOT' if match.get('is_bot') else 'NO'} | {'✓ VERIFIED' if match.get('is_verified') else 'NO'}\n"
                            f"[bold red]GROUP ROLE:[/bold red]      {'👑 ADMIN' if match.get('is_admin') else 'MEMBER'}\n"
                            f"[bold red]VOICE STATE:[/bold red]     {'🤐 MUTED' if match['muted'] else '🟢 SPEAKING'}\n"
                            f"[bold red]CAN SELF UNMUTE:[/bold red] {match.get('can_self_unmute')}\n"
                            f"[bold red]VOLUME LEVEL:[/bold red]    {match.get('volume') or 100}%\n"
                            f"[bold red]HAND RAISED:[/bold red]     {'✋ YES' if match.get('raise_hand') else 'NO'}\n"
                            f"[bold red]AUDIO SSRC:[/bold red]      {match.get('source')}\n"
                            f"[bold red]DATE JOINED:[/bold red]     {match.get('date_joined') or 'Live Session'}\n"
                            f"[bold red]CUSTOM ABOUT:[/bold red]    {match.get('about') or 'None'}"
                        )
                        console.print(Panel(dossier_text, title=f"[bold white]VC FORENSIC DOSSIER // #{target_uid}[/bold white]", border_style="red"))
                    else:
                        console.print("[yellow]Participant not found in current frequency roster.[/yellow]")
                    Prompt.ask("Press Enter to continue")
                elif sub_choice == "9":
                    import json
                    filename = f"vc_members_{group_id}_{int(time.time())}.json"
                    with open(filename, "w") as f:
                        json.dump(participants, f, indent=2)
                    console.print(f"[bold green]✓ Exported {len(participants)} VC member profiles to [bold white]{filename}[/bold white]![/bold green]")
                    await asyncio.sleep(1.5)

async def init_terminal():
    creds = Config.get_credentials()
    if not creds:
        console.print("[bold red]Missing credentials in .env![/bold red]")
        sys.exit(1)

    string_session = Config.get_string_session()
    if string_session:
        client = TelegramClient(StringSession(string_session), creds["api_id"], creds["api_hash"])
        await client.connect()
        if await client.is_user_authorized():
            return client

    client = TelegramClient(str(SESSION_FILE), creds["api_id"], creds["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        console.print(f"[yellow]🔐 One-time verification required for {creds['phone']}[/yellow]")
        await client.start(phone=creds["phone"])
        
        try:
            temp_s = StringSession()
            temp_s.set_dc(client.session.dc_id, client.session.server_address, client.session.port)
            temp_s.auth_key = client.session.auth_key
            s_str = temp_s.save()
            if s_str:
                Config.save_string_session(s_str)
        except Exception:
            pass

    return client

async def main():
    client = await init_terminal()
    db = DatabaseManager()
    engine = SuperBanEngine(client, db)
    app = TerminalApp(engine)
    try:
        await app.run()
    finally:
        await client.disconnect()
        db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
