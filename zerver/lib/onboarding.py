from typing import Dict, List

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.create_realm import setup_realm_internal_bots
from zerver.actions.message_send import (
    do_send_messages,
    internal_prep_stream_message_by_name,
    internal_send_private_message,
)
from zerver.actions.reactions import do_add_reaction
from zerver.lib.emoji import emoji_name_to_emoji_code
from zerver.lib.message import SendMessageRequest
from zerver.models import Message, Realm, UserProfile, get_system_bot


def missing_any_realm_internal_bots() -> bool:
    bot_emails = [
        bot["email_template"] % (settings.INTERNAL_BOT_DOMAIN,)
        for bot in settings.REALM_INTERNAL_BOTS
    ]
    bot_counts = {
        email: count
        for email, count in UserProfile.objects.filter(email__in=bot_emails)
        .values_list("email")
        .annotate(Count("id"))
    }
    realm_count = Realm.objects.count()
    return any(bot_counts.get(email, 0) < realm_count for email in bot_emails)


def create_if_missing_realm_internal_bots() -> None:
    """This checks if there is any realm internal bot missing.

    If that is the case, it creates the missing realm internal bots.
    """
    if missing_any_realm_internal_bots():
        for realm in Realm.objects.all():
            setup_realm_internal_bots(realm)


def send_initial_pms(user: UserProfile) -> None:
    organization_setup_text = ""

    # We need to override the language in this code path, because it's
    # called from account registration, which is a pre-account API
    # request and thus may not have the user's language context yet.
    with override_language(user.default_language):
        if user.is_realm_admin:
            help_url = user.realm.uri + "/help/getting-your-organization-started-with-zulip"
            organization_setup_text = (
                " " + _("We also have a guide for [Setting up your organization]({help_url}).")
            ).format(help_url=help_url)

        welcome_msg = _("Hello, and welcome to Zulip!") + "👋"
        demo_org_warning = ""
        if user.realm.demo_organization_scheduled_deletion_date is not None:
            demo_org_warning = (
                _(
                    "Note that this is a [demo organization]({demo_org_help_url}) and will be "
                    "**automatically deleted** in 30 days."
                )
                + "\n\n"
            )

        content = "".join(
            [
                welcome_msg + " ",
                _("This is a private message from me, Welcome Bot.") + "\n\n",
                _(
                    ":one: First of all, read [About]({about}) to know more about us!"
                ),
                "{organization_setup_text}" + "\n\n",
                "{demo_org_warning}",
                _(
                    ":two: Next fill up [your profile]({profile}). Example of nice profile you can see below this message. When you do it, just send me `profile ok`"
                )
                + "\n\n" +  "[](/static/images/cute/exellent_profile.png)" + "\n\n" ,
                _(
                    ":three: Read info about [streams we have](https://makeittogether.ru/#narrow/stream/1-general/topic/.D0.A1.D0.BF.D0.B8.D1.81.D0.BE.D0.BA.20.D0.BA.D0.B0.D0.BD.D0.B0.D0.BB.D0.BE.D0.B2/near/90)"
                ),
                _(
                    ":four: :warning: Please support us on [boosty](https://boosty.to/makeittogetherclub/purchase/1221202?ssource=DIRECT&share=subscription_link)"
                ),
                _("Here are a few messages I understand:") + " ",
                bot_commands(),
            ]
        )

    content = content.format(
        organization_setup_text=organization_setup_text,
        demo_org_warning=demo_org_warning,
        demo_org_help_url="/help/demo-organizations",
        getting_started_url="/help/getting-started-with-zulip",
        about="/policies/about",
        privacy="/policies/privacy",
        profile="#settings/profile",
    )

    internal_send_private_message(
        get_system_bot(settings.WELCOME_BOT, user.realm_id), user, content
    )


def bot_commands(no_help_command: bool = False) -> str:
    commands = [
        "apps",
        "profile",
        "theme",
        "streams",
        "topics",
        "message formatting",
        "keyboard shortcuts",
    ]
    if not no_help_command:
        commands.append("help")
    return ", ".join(["`" + command + "`" for command in commands]) + "."


def select_welcome_bot_response(human_response_lower: str) -> str:
    # Given the raw (pre-markdown-rendering) content for a private
    # message from the user to Welcome Bot, select the appropriate reply.
    if human_response_lower in ["app", "apps"]:
        return _(
            "You can [download](/apps) the [mobile and desktop apps](/apps). "
            "Zulip also works great in a browser."
        )
    elif human_response_lower == "profile":
        return _(
            "Go to [Profile settings](#settings/profile) "
            "to add a [profile picture](/help/change-your-profile-picture) "
            "and edit your [profile information](/help/edit-your-profile)."
        )
    elif human_response_lower == "theme":
        return _(
            "Go to [Display settings](#settings/display-settings) "
            "to [switch between the light and dark themes](/help/dark-theme), "
            "[pick your favorite emoji theme](/help/emoji-and-emoticons#change-your-emoji-set), "
            "[change your language](/help/change-your-language), "
            "and make other tweaks to your Zulip experience."
        )
    elif human_response_lower in ["stream", "streams", "channel", "channels"]:
        return "".join(
            [
                _(
                    "In Zulip, streams [determine who gets a message](/help/streams-and-topics). "
                    "They are similar to channels in other chat apps."
                )
                + "\n\n",
                _("[Browse and subscribe to streams](#streams/all)."),
            ]
        )
    elif human_response_lower in ["topic", "topics"]:
        return "".join(
            [
                _(
                    "In Zulip, topics [tell you what a message is about](/help/streams-and-topics). "
                    "They are light-weight subjects, very similar to the subject line of an email."
                )
                + "\n\n",
                _(
                    "Check out [Recent conversations](#recent) to see what's happening! "
                    'You can return to this conversation by clicking "Private messages" in the upper left.'
                ),
            ]
        )
    elif human_response_lower in ["keyboard", "shortcuts", "keyboard shortcuts"]:
        return "".join(
            [
                _(
                    "Zulip's [keyboard shortcuts](#keyboard-shortcuts) "
                    "let you navigate the app quickly and efficiently."
                )
                + "\n\n",
                _("Press `?` any time to see a [cheat sheet](#keyboard-shortcuts)."),
            ]
        )
    elif human_response_lower in ["formatting", "message formatting"]:
        return "".join(
            [
                _(
                    "Zulip uses [Markdown](/help/format-your-message-using-markdown), "
                    "an intuitive format for **bold**, *italics*, bulleted lists, and more. "
                    "Click [here](#message-formatting) for a cheat sheet."
                )
                + "\n\n",
                _(
                    "Check out our [messaging tips](/help/messaging-tips) "
                    "to learn about emoji reactions, code blocks and much more!"
                ),
            ]
        )
    elif human_response_lower in ["help", "?"]:
        return "".join(
            [
                _("Here are a few messages I understand:") + " ",
                bot_commands(no_help_command=True) + "\n\n",
                _(
                    "Check out our [Getting started guide](/help/getting-started-with-zulip), "
                    "or browse the [Help center](/help/) to learn more!"
                ),
            ]
        )
    elif human_response_lower in ["profile ok", "profile_ok"]:
        return "".join(
            [
                _(":three: Good job! Next go to [newcomers channel](#narrow/stream/4-newcomers)") + "\n\n",
                _(
                    "Create new topic and call it with your name and write about yourself" 
                ) + "\n\n" ,
                _(
                    "when you will done, send me `about ok`" 
                )  ,
            ]
        )
    elif human_response_lower in ["about ok", "about_ok"]:
        return "".join(
            [
                _("You are awesome, my friend :smile:. And now you should to join to team or create your own team.") + "\n\n", 
               
                _(
                    "So, if you want to join to team send me `join` and if you want to create your own team send me `create`"
                )  ,
            ]
        )
    elif human_response_lower in ["join", "join"]:
        return "".join(
            [
                _("Excellent. You choose to join to team. So go to [teams channel](#narrow/stream/3-teams) and on search box write your role (list of role you can see [here](https://makeittogether.ru/#narrow/stream/1-general/topic/.D0.A1.D0.BF.D0.B8.D1.81.D0.BE.D0.BA.20.D1.80.D0.BE.D0.BB.D0.B5.D0.B9/near/88)) ") + "\n\n",
                _(
                    "Then you get a list of teams which needs in that specialists. See description and choose team which relevant to you" 
                ) + "\n\n" ,
                _(
                    "When you will done all of that just write to me `all done`" 
                ) + "\n\n" ,
            ]
        )
    elif human_response_lower in ["all done", "alldone"]:
        return "".join(
            [
                _("[Cool ! So, what to do now?](https://media0.giphy.com/media/JUqiFbumTAPYIeM8yJ/giphy.gif?cid=e54532916d4wqwpgqrthxhsozdg6r2bj6oaa3tjde806lezt&rid=giphy.gif&ct=g) ") + "\n\n",
                _(
                    "First of all, wait to joining people to your team. Then create first video meeting to know your teammates, discuss your project and create some first issues (firts sprint)" 
                ) + "\n\n" ,
                _(
                    "Also decide how often you will meet with each other, create calendar entry." 
                ) + "\n\n" ,
                _(
                    "In meetings each member of team will:"
                ) + "\n\n" ,
                _(
                    "1. Tell what they did in previous week"
                ) + "\n\n" ,
                _(
                    "2. Discuss ideas and participate in problems solving"
                ) + "\n\n" ,
                _(
                    "3. Create issues to next sprint" 
                ) + "\n\n" ,
            ]
        )
    elif human_response_lower in ["create", "create"]:
        return "".join(
            [
                _("Excellent. You choose to create to team.") + "\n\n", 
                _(
                    "1. If you haven't an idea to what project to do with your team go to [projects channel](#narrow/stream/7-projects) and choose a project that you like" 
                ) + "\n\n" ,
                _(
                    "2. Go to [teams channel](#narrow/stream/3-teams)."
                ) + "\n\n" ,
                _(
                    "3. Create new topic in general chanel, you can call it with name of your project. In Message box write down why you did you choose this project and mention roles which you need in your team to complete your project. List of roles you can see [here](/policies/roles)" 
                ) + "\n\n" ,
                _(
                    "4. To communicate with each other you can create an group in private messages." 
                ) + "\n\n" ,
                _(
                    "When you will done all of that just write to me `all done`" 
                ) + "\n\n" ,
                
            ]
        ) 
    else:
        return "".join(
            [
                _(
                    "I’m sorry, I did not understand your message. Please try one of the following commands:"
                )
                + " ",
                bot_commands(),
            ]
        )


def send_welcome_bot_response(send_request: SendMessageRequest) -> None:
    """Given the send_request object for a private message from the user
    to welcome-bot, trigger the welcome-bot reply."""
    welcome_bot = get_system_bot(settings.WELCOME_BOT, send_request.message.sender.realm_id)
    human_response_lower = send_request.message.content.lower()
    content = select_welcome_bot_response(human_response_lower)
    internal_send_private_message(welcome_bot, send_request.message.sender, content)


@transaction.atomic
def send_initial_realm_messages(realm: Realm) -> None:
    welcome_bot = get_system_bot(settings.WELCOME_BOT, realm.id)
    # Make sure each stream created in the realm creation process has at least one message below
    # Order corresponds to the ordering of the streams on the left sidebar, to make the initial Home
    # view slightly less overwhelming
    content_of_private_streams_topic = (
        _("This is a private stream, as indicated by the lock icon next to the stream name.")
        + " "
        + _("Private streams are only visible to stream members.")
        + "\n"
        "\n"
        + _(
            "To manage this stream, go to [Stream settings]({stream_settings_url}) "
            "and click on `{initial_private_stream_name}`."
        )
    ).format(
        stream_settings_url="#streams/subscribed",
        initial_private_stream_name=Realm.INITIAL_PRIVATE_STREAM_NAME,
    )

    content1_of_topic_demonstration_topic = (
        _(
            "This is a message on stream #**{default_notification_stream_name}** with the "
            "topic `topic demonstration`."
        )
    ).format(default_notification_stream_name=Realm.DEFAULT_NOTIFICATION_STREAM_NAME)

    content2_of_topic_demonstration_topic = (
        _("Topics are a lightweight tool to keep conversations organized.")
        + " "
        + _("You can learn more about topics at [Streams and topics]({about_topics_help_url}).")
    ).format(about_topics_help_url="/help/streams-and-topics")

    content_of_swimming_turtles_topic = (
        _(
            "This is a message on stream #**{default_notification_stream_name}** with the "
            "topic `swimming turtles`."
        )
        + "\n"
        "\n"
        "[](/static/images/cute/turtle.png)"
        "\n"
        "\n"
        + _(
            "[Start a new topic]({start_topic_help_url}) any time you're not replying to a \
        previous message."
        )
    ).format(
        default_notification_stream_name=Realm.DEFAULT_NOTIFICATION_STREAM_NAME,
        start_topic_help_url="/help/start-a-new-topic",
    )

    welcome_messages: List[Dict[str, str]] = [
        {
            "stream": Realm.INITIAL_PRIVATE_STREAM_NAME,
            "topic": "private streams",
            "content": content_of_private_streams_topic,
        },
        {
            "stream": Realm.DEFAULT_NOTIFICATION_STREAM_NAME,
            "topic": "topic demonstration",
            "content": content1_of_topic_demonstration_topic,
        },
        {
            "stream": Realm.DEFAULT_NOTIFICATION_STREAM_NAME,
            "topic": "topic demonstration",
            "content": content2_of_topic_demonstration_topic,
        },
        {
            "stream": realm.DEFAULT_NOTIFICATION_STREAM_NAME,
            "topic": "swimming turtles",
            "content": content_of_swimming_turtles_topic,
        },
    ]

    messages = [
        internal_prep_stream_message_by_name(
            realm,
            welcome_bot,
            message["stream"],
            message["topic"],
            message["content"],
        )
        for message in welcome_messages
    ]
    message_ids = do_send_messages(messages)

    # We find the one of our just-sent messages with turtle.png in it,
    # and react to it.  This is a bit hacky, but works and is kinda a
    # 1-off thing.
    turtle_message = Message.objects.select_for_update().get(
        id__in=message_ids, content__icontains="cute/turtle.png"
    )
    (emoji_code, reaction_type) = emoji_name_to_emoji_code(realm, "turtle")
    do_add_reaction(welcome_bot, turtle_message, "turtle", emoji_code, reaction_type)
