# TV setup: Home Assistant + LG webOS integration

This is the one-time setup that makes `home.tv` commands actually do
something. After this, *"Hey Trusty, open YouTube on the TV"* works.

Trusty itself never talks to the TV directly, it asks Home Assistant,
and HA pairs with the LG TV over the LAN. Nothing about the TV leaves
your network.

---

## What you need


| Item              | Notes                                                                     |
| ----------------- | ------------------------------------------------------------------------- |
| LG webOS Smart TV | webOS 3.0 or later (any TV from ~2016 onwards)                            |
| TV powered on     | And connected to the same Wi-Fi / LAN as the computer running Trusty      |
| TV's IP address   | *Settings → Network → Connection Status* on the TV remote. Write it down. |
| HA running        | Already true: `docker compose ps` should show `trusty-homeassistant Up`   |


---

## Step-by-step

### Step 1: Open Home Assistant


| Where Trusty runs | URL                                                            |
| ----------------- | -------------------------------------------------------------- |
| Mac dev box       | [http://localhost:8123](http://localhost:8123)                 |
| Raspberry Pi      | [http://raspberrypi.local:8123](http://raspberrypi.local:8123) |


First boot takes ~1 minute. You'll see an onboarding wizard:

1. *Create my smart home*
2. Pick a name (anything, "Home" is fine)
3. Set country and time zone
4. **Create the admin account**: username + password. Write the password
  down somewhere safe; HA doesn't have email recovery.
5. Skip "Add devices for now" (we'll add the TV next).

You should land on the HA dashboard.

### Step 2: Add the LG webOS TV integration

1. Bottom-left → **Settings**
2. **Devices & Services**
3. Top right → **Add Integration**
4. Search for **"LG webOS Smart TV"**
5. Click it
6. Enter the TV's IP (the one from the TV's network settings)
7. Click **Submit**

#### What happens on the TV

A pairing prompt pops up on the TV screen:

> "Allow this device (Home Assistant) to control your TV?"

**Press OK on the TV remote.** You have ~30 seconds before it times out.
If you miss it, just hit **Submit** in HA again, it'll re-prompt.

#### Verify

In HA: **Settings → Devices & Services → LG webOS Smart TV**, you
should see one device listed. Click it and note the entity id, usually:

```
media_player.lg_webos_tv
```

If HA gave it a suffix (e.g. `media_player.lg_webos_tv_2`), copy that.

### Step 3: Generate a Long-Lived Access Token

Trusty needs a stable token to call HA's REST API.

1. In HA, click your **username** at the bottom-left
2. Scroll all the way down to **Long-Lived Access Tokens**
3. Click **Create Token**
4. Name it `trusty`
5. Click **OK**
6. **Copy the token shown.** This is the only time HA shows it. If you
  lose it, just create another.

The token is a long string starting with `eyJ...`.

### Step 4: Wire into `.env`

Open `.env` in the project root and set these three values:

```env
HA_URL=http://localhost:8123
HA_TOKEN=<paste the long token here>
LG_TV_ENTITY_ID=media_player.lg_webos_tv
```

Adjust:

- `HA_URL` to `http://raspberrypi.local:8123` if Trusty is on the Pi
- `LG_TV_ENTITY_ID` if HA gave you a suffix

### Step 5: Restart Trusty

```bash
pkill -f 'uvicorn app.main'
bash scripts/run_trusty.sh
```

(The voice loop and llama-server can keep running.)

### Step 6: Test

In the **Admin UI** at [http://localhost:8090/admin/](http://localhost:8090/admin/), open *Quick test*
and send:

```
Open YouTube on the TV
```

Expected reply: **"Opening YouTube on the TV."**
The TV should switch to the YouTube app.

If you get **"Sorry, I couldn't open YouTube on the TV right now. There
was an authorization error."**, see Troubleshooting below.

---

## Commands you can use once paired


| Spoken (or typed)                | What happens                                                                                  |
| -------------------------------- | --------------------------------------------------------------------------------------------- |
| Open YouTube on the TV           | switches to YouTube app                                                                       |
| Open Netflix on the TV           | switches to Netflix                                                                           |
| Turn off the TV                  | TV powers off                                                                                 |
| Turn on the TV                   | TV powers on (only works if the TV supports Wake-on-LAN, most LGs do; enable in TV settings)  |
| Lower the TV volume              | volume down                                                                                   |
| Mute the TV                      | volume_mute = true                                                                            |
| Show "dinner is ready" on the TV | toast notification on screen                                                                  |


The exact phrasing isn't fixed: Gemma's planner maps "the TV" / "on the TV"
/ "TV" to the `home.tv` tool. Be reasonable.

---

## Troubleshooting


| Symptom                                                       | Likely cause                                                            | Fix                                                                                                  |
| ------------------------------------------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `There was an authorization error.` on every TV command       | `HA_TOKEN` is missing, expired, or wrong                                | Create a fresh token (Step 3), update `.env`, restart Trusty                                         |
| `LG webOS Smart TV` integration doesn't appear in HA's search | HA version too old                                                      | Update HA: `docker compose pull homeassistant && docker compose up -d homeassistant`                 |
| TV pairing prompt never appears                               | TV is on a different VLAN/Wi-Fi than the host                           | Put both on the same network, or use a wired LAN                                                     |
| Pairing works once, then breaks                               | TV's stored client key got cleared (factory reset / power-off-at-mains) | Re-add the integration in HA, accept the new prompt                                                  |
| `Connection refused` to `http://localhost:8123`               | HA container not running                                                | `docker compose up -d homeassistant`, wait ~1 min                                                    |
| Mac only: TV not auto-discovered                              | Docker Desktop on macOS can't bridge SSDP/UPnP                          | You **must** add the TV by IP. Add a static DHCP lease on your router so the TV's IP doesn't change. |
| Pi: TV still not discovered after `network_mode: host`        | Wi-Fi router has client isolation enabled                               | Disable AP isolation in router settings, or wire the Pi to Ethernet on the same VLAN as the TV       |
| TV turns on but the app doesn't open                          | LG renamed the app channel                                              | Open the source list in HA and use the exact source name HA shows                                    |


---

## Privacy

Nothing about the TV leaves your network:

- HA talks to the TV directly over the LAN
- Trusty talks to HA over `http://localhost:8123`
- The TV's IP, content, viewing history: none of it leaves the device
- The `home.tv` tool's privacy ledger entry is `external_payload: none`

If you want to verify, watch `data/privacy_ledger.jsonl` while you issue
TV commands: every entry should have `internet_used: false`.