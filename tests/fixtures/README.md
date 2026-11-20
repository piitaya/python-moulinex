# Fixtures

Payloads of the SEB cloud used by the tests.

Captured from a real account with a Cookeo Touch Wifi (firmware 5.0.54), with the personal values replaced:

- `profile.json`: the profile of the account.
- `shadow_get_accepted.json` and `shadow_get_accepted_online.json`: the shadow of the Cookeo while it is turned off and turned on.
- `shadow_update_accepted_offline.json` and `shadow_update_documents_offline.json`: the two messages of the Cookeo being turned off.
- `shadow_update_accepted_online.json` and `shadow_update_documents_online.json`: the two messages of the Cookeo being turned on.
- `shadow_update_accepted_other.json` and `shadow_update_documents_other.json`: an update the Cookeo sends a few seconds after it turned on, which does not tell whether it is connected.
- `status_idle.json`: what the Cookeo answers when it is turned on and does nothing.
- `status_ready.json`, `status_alert_notification.json`, `status_alert.json`, `status_paused.json`, `status_warming.json`, `status_cooking.json` and `status_done.json`: a browning started and stopped by hand, in the order the messages came in.
- `status_recipe_preparation.json`, `status_recipe_ready.json`, `status_recipe_cooking.json` and `status_recipe_done.json`: steps of a recipe. The title and the identifiers of the recipe are replaced.
- `status_ingredient_warming.json` and `status_ingredient_done.json`: the cooking of an ingredient.

Not captured yet: a pressure cooking with its countdown (`remainingTime`), `depressurization`, `keep warm` and `delayed`.

Written by hand:

- `profile_two_appliances.json`: `profile.json` with a Companion, a Cookeo that is not connected and an appliance of another kind added. The identifiers of the Companion are made up.
