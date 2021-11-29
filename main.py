from datetime import datetime, timezone
from glob import glob
from uuid import uuid4

import sentry_sdk
from discord import (
    Activity,
    ActivityType,
    Color,
    Embed,
    Guild,
    Intents,
    Member,
    Message,
)
from discord.errors import Forbidden
from discord.ext.commands import Bot
from discord_slash import ComponentContext, MenuContext, SlashCommand, SlashContext
from discord_slash.model import ContextMenuType, SlashCommandOptionType
from discord_slash.utils.manage_commands import create_choice, create_option
from loguru import logger
from topgg import DBLClient

from config import CONFIG
from database import Block, Report
from utils import (
    REASONS_DICT,
    Permissions,
    ban_user,
    create_block,
    format_user_info,
    make_report_actionrows,
    send_report_embed,
)

sentry_sdk.init(CONFIG.sentry.dsn)

intents = Intents.default()
intents.members = True  # pylint: disable=assigning-non-slot
client = Bot(command_prefix=uuid4().hex, intents=intents)
slash = SlashCommand(
    client,
    # Uncomment if commands are added/removed or paramters are changed:
    # sync_commands=True,
)

client.topggpy = DBLClient(client, CONFIG.topgg.token)


async def post_guild_count():
    try:
        await client.topggpy.post_guild_count()
        logger.trace(f'Posted server count ({client.topggpy.guild_count})')
    except:
        logger.exception('Failed to post server count')


@client.event
async def on_ready():
    client.started = datetime.utcnow()
    await client.change_presence(
        activity=Activity(type=ActivityType.watching, name='/report')
    )
    await post_guild_count()
    logger.info(f'Ready as {client.user}')


@client.event
async def on_component(ctx: ComponentContext):
    if not ctx.custom_id.startswith('reportaction_'):
        return

    _, report_id, action = ctx.custom_id.split('_')

    report = Report.objects.get(id=report_id)  # pylint: disable=no-member

    if action == 'ignore':
        report.reviewed = True
        report.save()

        await ctx.edit_origin(
            content=f'Ignored by {ctx.author.mention}', components=[]
        )
    elif action == 'askinfo':
        user = await client.fetch_user(report.reporter_id)

        embed = Embed(
            title='Please provide us with more information',
            description=f"Hey, {user.mention}! We've reviewed your report and "
            'think we need a little more information or evidence.',
        )
        embed.add_field(
            name='Next steps',
            value='Please join our mail server at '
            f'https://discord.gg/{CONFIG.server.appeals_invite} and create a '
            'ticket to provide us more info. Thank you!',
        )
        embed.set_footer(text=f'Report ID: {report.id}')

        try:
            await user.send(embed=embed)

            await ctx.edit_origin(
                content='Successfully asked for more info by '
                f'{ctx.author.mention}',
                components=make_report_actionrows(
                    report_id, askinfo_disabled=True
                ),
            )
        except Forbidden:
            await ctx.edit_origin(
                content=f'Failed to ask for more info by {ctx.author.mention}',
                components=make_report_actionrows(
                    report_id, askinfo_disabled=True
                ),
            )

        report.reviewed = True
        report.save()
    elif action == 'block':
        reason = ctx.selected_options[0]

        await create_block(
            client,
            user_id=report.user_id,
            reason=reason,
            moderator_id=ctx.author.id,
        )

        report.reviewed = True
        report.save()

        await ctx.edit_origin(
            content=f'Blocked by {ctx.author.mention} for '
            f'{REASONS_DICT[reason]}',
            components=[],
        )


@client.event
async def on_guild_join(guild: Guild):
    logger.info(f'Joined guild {guild.name}')

    channel = client.get_channel(CONFIG.server.channels.server_joins)

    embed = Embed(title=f'Joined {guild.name}', color=Color.green())

    embed.set_thumbnail(url=guild.icon_url)

    embed.add_field(name='Member count', value=guild.member_count)
    embed.add_field(name='ID', value=guild.id)

    timestamp = int(guild.created_at.replace(tzinfo=timezone.utc).timestamp())
    embed.add_field(name='Created', value=f'<t:{timestamp}:R>')

    await channel.send(embed=embed)

    await post_guild_count()


@client.event
async def on_guild_remove(guild: Guild):
    logger.info(f'Left guild {guild.name}')

    channel = client.get_channel(CONFIG.server.channels.server_leaves)

    embed = Embed(title=f'Left {guild.name}', color=Color.red())

    embed.set_thumbnail(url=guild.icon_url)

    embed.add_field(name='Member count', value=guild.member_count)
    embed.add_field(name='ID', value=guild.id)

    timestamp = int(guild.created_at.replace(tzinfo=timezone.utc).timestamp())
    embed.add_field(name='Created', value=f'<t:{timestamp}:R>')

    await channel.send(embed=embed)

    await post_guild_count()


@client.event
async def on_member_join(member: Member):
    guild = member.guild

    try:
        # pylint: disable=no-member
        Block.objects.get(user_id=member.id)
        if guild.id not in CONFIG.noban_servers:
            logger.debug(f'{member} joined {guild}, banning due to block')
            await ban_user(client, guild, member)
    except Block.DoesNotExist:
        pass


@slash.slash(name='ping', description='See if the bot is alive')
async def ping_command(ctx: SlashContext):
    await ctx.send("I'm alive!", hidden=True)


@slash.slash(name='server', description='Join our community/support server')
async def server_command(ctx: SlashContext):
    await ctx.send(f'https://discord.gg/{CONFIG.server.invite}', hidden=True)


@slash.slash(name='appeal', description='Join our appeals server')
async def appeal_command(ctx: SlashContext):
    await ctx.send(
        f'https://discord.gg/{CONFIG.server.appeals_invite}', hidden=True
    )


@slash.slash(
    name='stats',
    description='See information about Blockbot',
    guild_ids=[CONFIG.server.id],
)
async def stats_command(ctx: SlashContext):
    await ctx.defer(hidden=True)

    embed = Embed(title='Blockbot Stats', color=Color.green())

    embed.set_thumbnail(url=client.user.avatar_url)

    embed.add_field(
        name='Server count', value=f'**`{len(client.guilds)}`** servers'
    )

    members = sum(guild.member_count for guild in client.guilds)
    embed.add_field(name='Total members', value=f'**`{members}`** members')

    # pylint: disable=no-member
    embed.add_field(
        name='Blocked', value=f'**`{Block.objects.count()}`** users'
    )
    embed.add_field(
        name='Reports', value=f'**`{Report.objects.count()}`** reports'
    )

    lines_of_code = sum(
        sum(line.strip() != '' for line in open(source_path))
        for source_path in glob('*.py')
    )
    embed.add_field(
        name='Code amount', value=f'**`{lines_of_code}`** lines of Python'
    )

    start_timestamp = int(
        client.started.replace(tzinfo=timezone.utc).timestamp()
    )
    embed.add_field(
        name='Uptime', value=f'Started **<t:{start_timestamp}:R>**'
    )

    await ctx.send(embed=embed, hidden=True)


@slash.slash(
    name='eval',
    description='Evaluates a Python expression',
    options=[
        create_option(
            name='expression',
            description='The expression to evaluate',
            option_type=SlashCommandOptionType.STRING,
            required=True,
        ),
    ],
    guild_ids=[CONFIG.server.id],
    permissions=Permissions.DEVELOPER_ONLY.value,
)
async def eval_command(ctx: SlashContext, expression: str):
    await ctx.defer(hidden=True)

    try:
        result = eval(expression)
        result_type = type(result)
        result_repr = repr(result)
        result_str = str(result)
    except Exception as exc:
        result = exc
        result_type = type(exc)
        result_repr = repr(exc)
        result_str = str(exc)

    embed = Embed(
        title='Eval',
        description=f'```py\n{result}\n```',
        color=Color.blurple(),
    )

    embed.add_field(
        name='Code', value=f'```py\n{expression}\n```', inline=False
    )
    embed.add_field(name='Type', value=f'**`{result_type.__name__}`**')
    embed.add_field(name='Repr', value=f'**`{result_repr}`**')
    embed.add_field(name='Str', value=f'**`{result_str}`**')

    await ctx.send(embed=embed, hidden=True)


@slash.slash(
    name='report',
    description="Report a user for breaking Discord's rules",
    options=[
        create_option(
            name='user',
            description='The user to report',
            option_type=SlashCommandOptionType.USER,
            required=True,
        ),
        create_option(
            name='evidence',
            description='Evidence to prove what the user did to violate '
            "Discord's terms of service or community guidelines",
            option_type=SlashCommandOptionType.STRING,
            required=True,
        ),
    ],
)
async def report_command(ctx: SlashContext, user: Member, evidence: str):
    logger.debug(f'{ctx.author} reported {user} for {evidence}')

    if isinstance(user, int):
        user = await client.fetch_user(user)

    await ctx.defer(hidden=True)

    if ctx.author == user:
        await ctx.send('You cannot report yourself.', hidden=True)
        return
    elif user == client.user or user.id in CONFIG.immune:
        await ctx.send(f'{user.mention} is immune to reporting.', hidden=True)
        return

    # pylint: disable=no-member
    if Report.objects(
        user_id=user.id, reporter_id=ctx.author.id, reviewed=False
    ):
        await ctx.send(f"You've already reported {user.mention}.", hidden=True)
        return

    report = Report(
        reason=evidence, user_id=user.id, reporter_id=ctx.author.id
    )
    report.save()

    await send_report_embed(
        client,
        reported=user,
        reporter=ctx.author,
        reason=evidence,
        timestamp=report.timestamp.replace(tzinfo=timezone.utc),
        report_id=str(report.id),
    )

    await ctx.send(
        f"{user.mention} has been reported for breaking Discord's rules.",
        hidden=True,
    )


@slash.slash(
    name='lookup',
    description='Finds global block information about a user',
    guild_ids=[CONFIG.server.id, CONFIG.server.appeals_id],
    options=[
        create_option(
            name='user',
            description='The user to find',
            option_type=SlashCommandOptionType.USER,
            required=True,
        )
    ],
    permissions=Permissions.GLOBAL_MOD_ONLY.value,
)
async def lookup_command(ctx: SlashContext, user: Member):
    logger.debug(f'Looking up {user}')

    if isinstance(user, int):
        user = await client.fetch_user(user)

    immune = user.id in CONFIG.immune

    # pylint: disable=no-member
    open_reports = bool(Report.objects(user_id=user.id, reviewed=False))

    # pylint: disable=no-member
    if Block.objects(user_id=user.id):
        blocked = True
        # pylint: disable=no-member
        block = Block.objects.get(user_id=user.id)
        reason = REASONS_DICT[block.reason]
        block_timestamp = block.timestamp.replace(tzinfo=timezone.utc)
        block_moderator = await client.fetch_user(block.moderator_id)
    else:
        blocked = False

    embed = Embed(color=Color.gold() if immune else Color.blurple())
    embed.set_author(name=str(user), icon_url=user.avatar_url)

    embed.add_field(
        name='Open reports',
        value='Yes' if open_reports else 'No',
        inline=False,
    )
    embed.add_field(name='Blocked', value='Yes' if blocked else 'No')

    if blocked:
        timestamp = int(
            block_timestamp.replace(tzinfo=timezone.utc).timestamp()
        )

        embed.add_field(
            name='Block reason', value=f'{reason}\n(<t:{timestamp}:R>)'
        )
        embed.add_field(
            name='Blocking moderator', value=format_user_info(block_moderator)
        )

    await ctx.send(embed=embed, hidden=True)


@slash.slash(
    name='block',
    description='Block a user without a report',
    guild_ids=[CONFIG.server.id, CONFIG.server.appeals_id],
    options=[
        create_option(
            name='user',
            description='The user to block',
            option_type=SlashCommandOptionType.USER,
            required=True,
        ),
        create_option(
            name='reason',
            description='Reason for blocking',
            option_type=SlashCommandOptionType.STRING,
            required=True,
            choices=[
                create_choice(name=name, value=value)
                for value, name in CONFIG.reasons
            ],
        ),
    ],
    permissions=Permissions.GLOBAL_MOD_ONLY.value,
)
async def block_command(ctx: SlashContext, user: Member, reason: str):
    logger.debug(f'{ctx.author} blocked {user} for {reason}')

    if isinstance(user, int):
        user = await client.fetch_user(user)

    await ctx.defer(hidden=True)

    await create_block(
        client,
        user_id=user.id,
        reason=reason,
        moderator_id=ctx.author.id,
    )

    await ctx.send(f'Blocked {user.mention}', hidden=True)


@slash.slash(
    name='massblock',
    description='Block multiple users without a report',
    guild_ids=[CONFIG.server.id, CONFIG.server.appeals_id],
    options=[
        create_option(
            name='user_ids',
            description='The space-separated user IDs to block',
            option_type=SlashCommandOptionType.STRING,
            required=True,
        ),
        create_option(
            name='reason',
            description='Reason for blocking',
            option_type=SlashCommandOptionType.STRING,
            required=True,
            choices=[
                create_choice(name=name, value=value)
                for value, name in CONFIG.reasons
            ],
        ),
    ],
    permissions=Permissions.GLOBAL_MOD_ONLY.value,
)
async def mass_block_command(ctx: SlashContext, user_ids: str, reason: str):
    logger.debug(f'{ctx.author} mass-blocked for {reason}')

    await ctx.defer(hidden=True)

    users = [
        await client.fetch_user(int(user_id))
        for user_id in user_ids.split(' ')
    ]

    for user in users:
        await create_block(
            client,
            user_id=user.id,
            reason=reason,
            moderator_id=ctx.author.id,
        )

    await ctx.send(
        f'Blocked {", ".join(user.mention for user in users)}', hidden=True
    )


@slash.context_menu(
    target=ContextMenuType.MESSAGE,
    name='Report message',
)
async def report_message(ctx: MenuContext):
    message: Message = ctx.target_message
    user: Member = message.author
    reason: str = message.content or '*`No message content`*'

    logger.debug(f'{ctx.author} reported {user} for {reason}')

    await ctx.defer(hidden=True)

    if ctx.author == user:
        await ctx.send('You cannot report yourself.', hidden=True)
        return
    elif user == client.user or user.id in CONFIG.immune:
        await ctx.send(f'{user.mention} is immune to reporting.', hidden=True)
        return

    # pylint: disable=no-member
    if Report.objects(
        user_id=user.id, reporter_id=ctx.author.id, reviewed=False
    ):
        await ctx.send(f"You've already reported {user.mention}.", hidden=True)
        return
    elif Report.objects(message_id=message.id):
        await ctx.send('This message has already been reported.', hidden=True)
        return

    report = Report(
        reason=reason,
        user_id=user.id,
        reporter_id=ctx.author.id,
        message_id=message.id,
    )
    report.save()

    await send_report_embed(
        client,
        reported=user,
        reporter=ctx.author,
        reason=reason,
        timestamp=report.timestamp.replace(tzinfo=timezone.utc),
        report_id=str(report.id),
        message=True,
    )

    await ctx.send(
        f"{user.mention} has been reported for breaking Discord's rules. Note "
        'that we currently cannot view attachments of reported messages, so '
        f'please join https://discord.gg/{CONFIG.server.invite} if the '
        'attachments of this message are relevant to your report.',
        hidden=True,
    )


client.run(CONFIG.bot.token)
