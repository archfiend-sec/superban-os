import asyncio
import time
from typing import List, Dict, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.layout import Layout
from rich.live import Live

from core.engine import SuperBanEngine, OperationState
from core.forensics import ForensicInspector

console = Console()

class RichTUI:
    def __init__(self, engine: SuperBanEngine):
        self.engine = engine

    def print_banner(self):
        console.print("""
[bold cyan]╔═══════════════════════════════════════════════════════════════╗[/bold cyan]
[bold cyan]║[/bold cyan]   [bold white]🚀 SUPERBAN OS[/bold white] [bold magenta]//[/bold magenta] [bold green]TELEGRAM MISSION CONTROL v3.2[/bold green]          [bold cyan]║[/bold cyan]
[bold cyan]║[/bold cyan]   [dim]Forensic Intelligence • Adaptive Circuit Breakers • Multi-Action[/dim] [bold cyan]║[/bold cyan]
[bold cyan]╚═══════════════════════════════════════════════════════════════╝[/bold cyan]
""", justify="center")

    async def run(self):
        self.print_banner()
        me = await self.engine.client.get_me()
        console.print(f"[bold green]● Connected Account:[/bold green] {me.first_name} ([cyan]{me.phone}[/cyan]) | ID: {me.id}\n")

        while True:
            console.print(Panel(
                "[bold cyan]1.[/bold cyan] 🎯 [bold white]Execute Tactical Action (Ban / Kick / Mute / Unban)[/bold white]\n"
                "[bold cyan]2.[/bold cyan] 🔬 [bold white]Deep Forensic Group Audit (Ghosts, Scams, Demographics)[/bold white]\n"
                "[bold cyan]3.[/bold cyan] 👻 [bold white]Ghost Buster: Auto-Purge Inactive Accounts[/bold white]\n"
                "[bold cyan]4.[/bold cyan] 🚨 [bold white]Scam Raider Annihilator: Auto-Purge Crypto/Spam Handles[/bold white]\n"
                "[bold cyan]5.[/bold cyan] 🗑️  [bold white]Safe Batch Message History Purge (100 msgs/batch)[/bold white]\n"
                "[bold cyan]6.[/bold cyan] 📈 [bold white]Global Audit Statistics & Whitelist Management[/bold white]\n"
                "[bold cyan]7.[/bold cyan] 🚪 [bold white]Exit[/bold white]",
                title="[bold yellow]MISSION CONTROL ARSENAL[/bold yellow]",
                border_style="bright_blue"
            ))

            choice = Prompt.ask("[bold yellow]Enter command option[/bold yellow]", choices=["1", "2", "3", "4", "5", "6", "7"], default="1")

            try:
                if choice == "1":
                    await self.action_flow()
                elif choice == "2":
                    await self.forensic_flow()
                elif choice == "3":
                    await self.ghost_buster_flow()
                elif choice == "4":
                    await self.scam_annihilator_flow()
                elif choice == "5":
                    await self.delete_history_flow()
                elif choice == "6":
                    await self.stats_flow()
                elif choice == "7":
                    console.print("[yellow]Shutting down Mission Control. Goodbye![/yellow]")
                    return
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted by user.[/yellow]")
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")

    async def select_group(self):
        groups = await self.engine.get_dialogs()
        if not groups:
            console.print("[red]No groups or channels found on this account.[/red]")
            return None

        table = Table(title="[bold cyan]Select Target Group[/bold cyan]", border_style="dim")
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("Title", style="white")
        table.add_column("ID", style="dim")
        table.add_column("Type", style="green")

        for idx, g in enumerate(groups[:25], 1):
            g_type = "Channel" if g["is_channel"] else "Supergroup"
            table.add_row(str(idx), g["title"], str(g["id"]), g_type)

        console.print(table)
        choice = IntPrompt.ask("Select group index", default=1)
        if 1 <= choice <= len(groups):
            return groups[choice - 1]
        return None

    async def action_flow(self):
        group = await self.select_group()
        if not group: return

        action = Prompt.ask("[bold yellow]Choose Action[/bold yellow]", choices=["ban", "kick", "mute", "unban"], default="ban")
        mode = Prompt.ask("[bold yellow]Choose Speed Mode[/bold yellow]", choices=["stealth", "normal", "fast", "turbo", "nuclear"], default="normal")
        limit = IntPrompt.ask("Max participants to scan", default=3000)

        with console.status("[bold green]Scanning group members...[/bold green]"):
            members = await self.engine.inspect_group_members(group["entity"], limit=limit)

        console.print(f"[green]✓ Scanned {len(members)} participants.[/green]")
        if not Confirm.ask(f"Ready to execute [bold red]{action.upper()}[/bold red] on {len(members)} accounts in {mode.upper()} mode?"):
            return

        await self.run_progress_tracker(group["entity"], members, action=action, mode=mode)

    async def ghost_buster_flow(self):
        group = await self.select_group()
        if not group: return

        days = IntPrompt.ask("Minimum days offline to qualify as Ghost", default=30)
        with console.status("[bold green]Hunting ghost accounts...[/bold green]"):
            members = await self.engine.inspect_group_members(group["entity"], limit=5000)

        ghosts = [m for m in members if (m['days_offline'] and m['days_offline'] >= days) or m['ghost_score'] >= 60]
        console.print(f"[bold purple]👻 Identified {len(ghosts)} ghost accounts[/bold purple] (inactive ≥ {days} days or deleted).")

        if not ghosts:
            console.print("[green]No ghost accounts matching criteria.[/green]")
            return

        if Confirm.ask(f"Proceed with Ghost Buster purge of {len(ghosts)} accounts?"):
            await self.run_progress_tracker(group["entity"], ghosts, action="ban", mode="normal")

    async def scam_annihilator_flow(self):
        group = await self.select_group()
        if not group: return

        with console.status("[bold green]Profiling accounts for scam indicators...[/bold green]"):
            members = await self.engine.inspect_group_members(group["entity"], limit=5000)

        scammers = [m for m in members if m['is_suspicious']]
        console.print(f"[bold red]🚨 Flagged {len(scammers)} suspicious spam/crypto accounts.[/bold red]")

        if not scammers:
            console.print("[green]No suspicious accounts detected![/green]")
            return

        if Confirm.ask(f"Purge all {len(scammers)} flagged accounts?"):
            await self.run_progress_tracker(group["entity"], scammers, action="ban", mode="fast")

    async def forensic_flow(self):
        group = await self.select_group()
        if not group: return

        with console.status("[bold green]Running deep forensic audit...[/bold green]"):
            members = await self.engine.inspect_group_members(group["entity"], limit=5000)

        total = len(members)
        bots = sum(1 for m in members if m['is_bot'])
        deleted = sum(1 for m in members if m['is_deleted'])
        premium = sum(1 for m in members if m['is_premium'])
        ghosts = sum(1 for m in members if m['is_ghost'])
        suspicious = sum(1 for m in members if m['is_suspicious'])

        table = Table(title=f"Forensic Audit Report - {group['title']}", border_style="bright_blue")
        table.add_column("Metric", style="white")
        table.add_column("Count", style="cyan", justify="right")
        table.add_column("Percentage", style="yellow", justify="right")

        table.add_row("Total Scanned Members", str(total), "100.0%")
        table.add_row("👻 Ghost Accounts (Inactive)", str(ghosts), f"{(ghosts/total*100) if total else 0:.1f}%")
        table.add_row("🚨 Suspicious / Scam Risk", str(suspicious), f"{(suspicious/total*100) if total else 0:.1f}%")
        table.add_row("🤖 Bot Integrations", str(bots), f"{(bots/total*100) if total else 0:.1f}%")
        table.add_row("💀 Deleted / Dead Profiles", str(deleted), f"{(deleted/total*100) if total else 0:.1f}%")
        table.add_row("⭐ Telegram Premium Users", str(premium), f"{(premium/total*100) if total else 0:.1f}%")

        console.print(table)

    async def delete_history_flow(self):
        group = await self.select_group()
        if not group: return

        days = IntPrompt.ask("Delete messages from past how many days", default=7)
        if Confirm.ask(f"[bold red]Confirm deleting all messages from the last {days} days?[/bold red]"):
            from datetime import datetime, timedelta
            cutoff = datetime.now() - timedelta(days=days)
            batch = []
            deleted_total = 0

            with console.status("[bold red]Purging messages in batches...[/bold red]"):
                async for msg in self.engine.client.iter_messages(group["entity"], offset_date=cutoff):
                    batch.append(msg.id)
                    if len(batch) >= 100:
                        await self.engine.client.delete_messages(group["entity"], batch)
                        deleted_total += len(batch)
                        batch.clear()
                        await asyncio.sleep(0.8)

                if batch:
                    await self.engine.client.delete_messages(group["entity"], batch)
                    deleted_total += len(batch)

            console.print(f"[bold green]✓ Successfully purged {deleted_total} messages.[/bold green]")

    async def stats_flow(self):
        stats = await self.engine.db.get_stats()
        console.print(Panel(
            f"[bold cyan]Total Actions Logged:[/bold cyan] {stats['total_actions']}\n"
            f"[bold green]Successful Actions:[/bold green]   {stats['successful_actions']}\n"
            f"[bold red]Failed Actions:[/bold red]       {stats['failed_actions']}\n"
            f"[bold yellow]Completed Operations:[/bold yellow] {stats['completed_sessions']}\n"
            f"[bold magenta]Protected Whitelist:[/bold magenta]  {stats['whitelisted_users']}",
            title="Global Database Telemetry", border_style="cyan"
        ))

    async def run_progress_tracker(self, group_entity, targets: List[Dict[str, Any]], action: str, mode: str):
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
            await self.engine.run_operation(group_entity, targets, action=action, mode=mode)
            progress.update(task_id, completed=total)

        console.print("[bold green]✓ Operation execution finished![/bold green]\n")
