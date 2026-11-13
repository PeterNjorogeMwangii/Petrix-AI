from app.workflows.actions.send_attachment.files import link_metadata
from app.workflows.chat_script import ScriptError
from app.workflows.templates import render


def execute(config, context):
    if context.carousel_calls >= 10: raise ScriptError("A turn can send up to 10 carousels.")
    context.carousel_calls += 1
    translations = config["translations"]
    selected = translations.get(context.language) or translations.get(context.agent.default_language) or next(iter(translations.values()))
    cards = []
    for card in selected["cards"]:
        image = None
        if card["image"]:
            source = card["image"]
            try:
                if source["source"] == "upload":
                    if context.attachment_store is None: raise ScriptError("Image storage is unavailable.")
                    image = context.attachment_store.carousel_image(source["asset_id"])
                else:
                    image = link_metadata(source["url"], "image")
            except ValueError as error:
                raise ScriptError(str(error)) from None
        buttons = [{**button, "label": render(button["label"], context, maximum=80),
            "message": render(button["message"], context, maximum=1000) if button["type"] == "reply" else ""}
            for button in card["buttons"]]
        cards.append({"id": card["id"], "title": render(card["title"], context, maximum=80),
            "subtitle": render(card["subtitle"], context, maximum=200) if card["subtitle"] else "",
            "image": image, "buttons": buttons})
    return {"content": "", "quick_replies": [], "carousel": {"cards": cards}}
