from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List

from discord import Client, Color, Embed, Guild, Member
from discord.errors import Forbidden, NotFound
from discord_slash.model import ButtonStyle, SlashCommandPermissionType
from discord_slash.utils.manage_commands import create_permission
from discord_slash.utils.manage_components import (
    create_actionrow,
    create_button,
    create_select,
    create_select_option,
)
from loguru import logger

from config import CONFIG
from database import Block

REASONS_DICT = dict(CONFIG.reasons)


def make_report_actionrows(
    report_id: str, *, askinfo_disabled: bool = False
) -> List[Dict]:
    return [
        create_actionrow(
            create_select(
                options=[
                    create_select_option(title, value=value)
                    for value, title in CONFIG.reasons
                ],
                placeholder='Global block',
                min_values=1,
                max_values=1,
                custom_id=f'reportaction_{report_id}_block',
            )
        ),
        create_actionrow(
            create_button(
                style=ButtonStyle.green,
                label='Ignore',
                custom_id=f'reportaction_{report_id}_ignore',
            ),
            create_button(
                style=ButtonStyle.blurple,
                label='Ask for more info',
                custom_id=f'reportaction_{report_id}_askinfo',
                disabled=askinfo_disabled,
            ),
        ),
    ]


class Permissions(Enum):
    GLOBAL_MOD_ONLY = {
        CONFIG.server.id: [
            create_permission(
                CONFIG.server.roles.global_mod,
                SlashCommandPermissionType.ROLE,
                True,
            ),
            create_permission(
                CONFIG.server.roles.everyone,
                SlashCommandPermissionType.ROLE,
                False,
            ),
        ]
    }
    DEVELOPER_ONLY = {
        CONFIG.server.id: [
            create_permission(
                CONFIG.server.roles.developer,
                SlashCommandPermissionType.ROLE,
                True,
            ),
            create_permission(
                CONFIG.server.roles.everyone,
                SlashCommandPermissionType.ROLE,
                False,
            ),
        ]
    }


async def send_report_embed(
    client: Client,
    *,
    reported: Member,
    reporter: Member,
    reason: str,
    timestamp: datetime,
    report_id: str,
    message: bool = False,
):
    channel = client.get_channel(CONFIG.server.channels.reports)

    embed = Embed(
        title='New report',
        color=Color.gold(),
        timestamp=timestamp.replace(tzinfo=timezone.utc),
    )
    embed.add_field(
        name='Reported',
        value=f'{reported.mention}\n`{reported}`\n`{reported.id}`',
        inline=False,
    )
    embed.add_field(
        name='Reporter',
        value=f'{reporter.mention}\n`{reporter}`\n`{reporter.id}`',
        inline=False,
    )
    embed.add_field(
        name='Reported message' if message else 'Reason',
        value=reason,
        inline=False,
    )
    embed.set_footer(text=report_id)

    await channel.send(
        '@here',
        embed=embed,
        components=make_report_actionrows(report_id),
    )


async def create_block(
    client: Client, *, user_id: int, reason: str, moderator_id: int
) -> Block:
    block = Block(
        user_id=user_id,
        reason=reason,
        moderator_id=moderator_id,
    )
    block.save()

    user = await client.fetch_user(user_id)
    moderator = await client.fetch_user(moderator_id)

    embed = Embed(
        title='Global block created',
        description="Due to a violation of Discord's rules, you've been globally banned from all servers Blockbot is in and reported to Discord's Trust & Safety team.",
        color=Color.dark_red(),
    )

    embed.add_field(name='Reason', value=REASONS_DICT[reason])
    embed.add_field(
        name='Appeal',
        value=f'https://discord.gg/{CONFIG.server.appeals_invite}',
    )

    embed.set_footer(text=f'User ID: {user_id}')

    try:
        await user.send(embed=embed)
    except Forbidden:
        logger.warning(f'Failed to send message to {user}')

    channel = client.get_channel(CONFIG.server.channels.block_logs)

    embed = Embed(
        title='New block',
        color=Color.dark_red(),
        timestamp=block.timestamp.replace(tzinfo=timezone.utc),
    )
    embed.add_field(
        name='User', value=f'{user.mention}\n`{user}`\n`{user.id}`'
    )
    embed.add_field(
        name='Moderator',
        value=f'{moderator.mention}\n`{moderator}`\n`{moderator.id}`',
    )
    embed.add_field(name='Reason', value=REASONS_DICT[reason])

    await channel.send(embed=embed)

    for guild in client.guilds:
        if guild.id not in CONFIG.noban_servers:
            try:
                member = await guild.fetch_member(user_id)
                await ban_user(client, guild, member)
            except NotFound:
                continue


async def ban_user(client: Client, guild: Guild, user: Member):
    block = Block.objects.get(user_id=user.id)   # pylint: disable=no-member

    moderator = client.get_user(block.moderator_id)

    logger.debug(f'Banning {user} from {guild}')

    await guild.ban(
        user,
        reason=f'Global block by {moderator} ({moderator.id})\n\n{REASONS_DICT[block.reason]}',
    )

    embed = Embed(
        title=f'Banned from {guild.name}',
        description="You were banned from this server due to your global block for violating Discord's rules.",
    )
    embed.add_field(name='Reason', value=REASONS_DICT[block.reason])
    embed.add_field(
        name='Appeal',
        value=f'https://discord.gg/{CONFIG.server.appeals_invite}',
    )

    embed.set_footer(text=f'User ID: {user.id}')

    try:
        await user.send(embed=embed)
    except Forbidden:
        logger.warning(f'Failed to send message to {user}')
