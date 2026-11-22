
PRODUCT = "AJ CINE AI"

COMPANY = "AJ APPLICATIONS"
DEVELOPER = "Ahammed Ajmal S"

VERSION = "2.5.0"

CREDIT_LINE = f"Developed by {COMPANY}"
DEVELOPER_LINE = f"Developer: {DEVELOPER}"
FOOTER = f"{CREDIT_LINE} · {DEVELOPER_LINE}"

VERSION = "2.5.0"

CREDIT_LINE = f"Developed by AJ APPLICATIONS"
DEVELOPER_LINE = f"Developer: Ahammed Ajmal S"
FOOTER = f"{CREDIT_LINE} · {DEVELOPER_LINE}"

ABOUT_TEXT = (
    f"<b>{PRODUCT}</b> v{VERSION}\n"
    "AI-assisted movie and TV series finder for Telegram.\n\n"
    f"<b>{CREDIT_LINE}</b>\n"
    f"<b>{DEVELOPER_LINE}</b>\n\n"
    "Titles are identified and verified through TMDB. "
    "Only content the operator is authorized to distribute is indexed."
)

START_TEXT = (
    f"<b>{PRODUCT}</b>\n"
    "Send me a movie or series name and I will find it.\n"
    "Typos, Manglish and partial names are fine.\n\n"
    f"<i>{CREDIT_LINE} · {DEVELOPER_LINE}</i>"
)

HELP_TEXT = (
    "<b>How to use this bot</b>\n"
    "Send the name of a movie or series. Typos and partial names are fine.\n\n"
    "Pick the title, then the language and quality. Series are split by season and episode.\n"
    "Tap <b>Get file in private chat</b> and the bot sends the file to you.\n"
    "Tap <b>Add to my list</b> on any title to save it for later.\n"
    "If a title is not found, tap <b>Request this title</b>.\n\n"
    "<b>Commands</b>\n"
    "/start - main menu\n"
    "/help - this message\n"
    "/trending - most opened titles\n"
    "/mylist - your saved titles\n"
    "/recent - recently added titles\n\n"
    f"<i>{CREDIT_LINE} · {DEVELOPER_LINE}</i>"
)
