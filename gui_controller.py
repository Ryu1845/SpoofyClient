import re
from threading import RLock

import requests
import wx
import wx.adv
import wx.lib.newevent

from gui_view import SpoofyLoginDialog, SpoofyStatusDialog, AboutDialog
from utils import resource_path
from spotify_controller import SpotifyController, LogTarget

LOG_MSG_FORMAT = re.compile(r"\[.*?] (.*)")

BITRATE_CHOICES = {
    0: 96,
    1: 160,
    2: 320
}

LogEvent, EVT_LOG = wx.lib.newevent.NewEvent()
SpotifyEvent, EVT_SPOTIFY = wx.lib.newevent.NewEvent()
BotEvent, EVT_BOT = wx.lib.newevent.NewEvent()


class LogTextboxTarget(LogTarget):
    def __init__(self, client: 'SpoofyClientApp'):
        self.client: 'SpoofyClientApp' = client

    def process(self, message):
        # Filter out log tags from message
        with self.client.gui_update_lock:
            if m := LOG_MSG_FORMAT.match(message):
                evt = LogEvent(msg=m.group(1))
            else:
                evt = LogEvent(msg=message)
            wx.PostEvent(self.client, evt)


class LibrespotOutputProcessorTarget(LogTarget):
    AUTH_ERROR_RE = re.compile(r"\[.*?] Could not connect to server: Authentication failed with error: (.*)")
    AUTH_SUCCESS_RE = re.compile(r"\[.*?] Authenticated as \"(.*)\" !")

    def __init__(self, client: 'SpoofyClientApp'):
        self.client: 'SpoofyClientApp' = client

    def process(self, message):
        # Detect errors in librespot output
        with self.client.gui_update_lock:
            if m := self.AUTH_ERROR_RE.match(message):
                auth_error = SpotifyEvent(evt_type="auth_error", err_msg=m.group(1))
                wx.PostEvent(self.client, auth_error)

            elif m := self.AUTH_SUCCESS_RE.match(message):
                auth_success = SpotifyEvent(evt_type="auth_success", username=m.group(1))
                wx.PostEvent(self.client, auth_success)


class SpoofyTaskBarIcon(wx.adv.TaskBarIcon):
    def __init__(self, frame):
        wx.adv.TaskBarIcon.__init__(self)
        self.frame = frame
        self.icon = wx.Icon(resource_path("res/spoofy_small.png"), type=wx.BITMAP_TYPE_PNG)
        self.SetIcon(self.icon, "Restore")
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self.OnTaskBarLeftClick)

    def OnTaskBarActivate(self, evt):
        """"""
        pass

    def OnTaskBarClose(self, evt):
        """
        Destroy the taskbar icon and frame from the taskbar icon itself
        """
        self.frame.Close()

    # ----------------------------------------------------------------------
    def OnTaskBarLeftClick(self, evt):
        """
        Create the right-click menu
        """
        self.frame.on_taskbar_restore()


class SpoofyClientApp(wx.App):
    def OnInit(self):
        self.gui_update_lock = RLock()
        self.login_window = SpoofyLoginDialog(None, wx.ID_ANY, "")
        self.status_window = SpoofyStatusDialog(None, wx.ID_ANY, "")
        self.about_window = AboutDialog(None, wx.ID_ANY, "")
        self.login_window.Show()
        self.taskbar_icon = None
        self.spotify_client = None

        # Status variables
        self.minimized = False
        self.username = None
        self.password = None
        self.bitrate = None

        with self.gui_update_lock:
            self.SetTopWindow(self.status_window)

            # Link new log message entry events
            self.Bind(EVT_LOG, self.on_log_event)

            # Link Spotify Event handler
            self.Bind(EVT_SPOTIFY, self.on_spotify_event)

            # Link Bot Event handler
            self.Bind(EVT_BOT, self.on_bot_event)

            # Link on window close handlers
            self.login_window.Bind(wx.EVT_CLOSE, self.on_login_window_close)
            self.status_window.Bind(wx.EVT_CLOSE, self.on_status_window_close)

            # Link login window buttons
            self.login_window.login_button.Bind(wx.EVT_BUTTON, self.on_login_clicked)
            self.login_window.Bind(wx.EVT_CHAR_HOOK, self.on_login_window_key_up)

            # Link status window buttons
            self.status_window.exit_button.Bind(wx.EVT_BUTTON, self.on_exit_clicked)
            self.status_window.about_button.Bind(wx.EVT_BUTTON, self.on_about_clicked)
            self.status_window.log_out_button.Bind(wx.EVT_BUTTON, self.on_logout_clicked)
            self.status_window.minimize_button.Bind(wx.EVT_BUTTON, self.on_minimize_clicked)
            self.status_window.connect_button.Bind(wx.EVT_BUTTON, self.on_connect_clicked)
            self.status_window.Bind(wx.EVT_CHAR_HOOK, self.on_status_window_key_up)

            # Link about window buttons
            self.about_window.close_button.Bind(wx.EVT_BUTTON, self.on_about_close_clicked)

            # Update version label in login view and about view, and github urls
            from main import CLIENT_VERSION, GITHUB_LINK_BOT, GITHUB_LINK_CLIENT
            self.login_window.title.SetLabel(f"Spoofy Client {CLIENT_VERSION}")
            self.about_window.title.SetLabel(f"Spoofy Client {CLIENT_VERSION}")
            self.about_window.label_version_current.SetLabel(f"Current version: {CLIENT_VERSION}")
            self.about_window.link_client.SetLabel(GITHUB_LINK_CLIENT)
            self.about_window.link_client.SetURL(GITHUB_LINK_CLIENT)
            self.about_window.link_bot.SetLabel(GITHUB_LINK_BOT)
            self.about_window.link_bot.SetURL(GITHUB_LINK_BOT)

            return True

    def log(self, message):
        with self.gui_update_lock:
            cur_log = self.status_window.log_text.GetValue().split("\n")
            new_log = [message] + cur_log[:1000]
            self.status_window.log_text.SetValue("\n".join(new_log))
            self.status_window.log_text.SetInsertionPoint(0)

    def update_spotify_status(self, state, msg):
        with self.gui_update_lock:
            self.status_window.status_spotify_icon.SetBitmap(wx.Bitmap(resource_path(f"res/{state}.png"), wx.BITMAP_TYPE_ANY))
            self.status_window.status_spotify_label.SetLabel(f"Spotify: {msg}")

    def update_bot_status(self, state, msg):
        with self.gui_update_lock:
            self.status_window.status_bot_icon.SetBitmap(wx.Bitmap(resource_path(f"res/{state}.png"), wx.BITMAP_TYPE_ANY))
            self.status_window.status_bot_label.SetLabel(f"Spoofy Bot: {msg}")

    def check_latest_version(self):
        from main import GITHUB_LATEST_RELEASE_API_URL
        with self.gui_update_lock:
            res = requests.get(GITHUB_LATEST_RELEASE_API_URL)
            if res.status_code == 200:
                data = res.json()
                if 'tag_name' in data:
                    self.about_window.label_version_latest.SetLabel(f"Latest version: {data['tag_name']}")
                else:
                    self.about_window.label_version_latest.SetLabel(f"Latest version: Unknown (error checking)")
            else:
                self.about_window.label_version_latest.SetLabel(f"Latest version: Unknown (error checking)")

    def clear_spotify_client(self):
        if self.spotify_client is not None:
            self.spotify_client.stop()
            self.spotify_client.wait()
            self.spotify_client = None

    def on_login_clicked(self, event):
        with self.gui_update_lock:
            self.login_window.login_button.Disable()
            self.username = self.login_window.username.GetValue()
            self.password = self.login_window.password.GetValue()
            self.bitrate = BITRATE_CHOICES.get(self.login_window.bitrate.GetSelection(), 160)

            # Clear pw field
            self.login_window.password.SetValue("")

            # Setup spotify connection
            print("Starting spotify client...")
            self.log("Starting spotify client...")
            self.spotify_client = SpotifyController.create(self, self.username, self.password, self.bitrate)
            self.spotify_client.log_targets.append(LogTextboxTarget(client=self))
            self.spotify_client.log_targets.append(LibrespotOutputProcessorTarget(client=self))
            result, msg, short_msg = self.spotify_client.check_req(self.username)

            if not result:
                if not msg:
                    print("Not ok to connect, no linked account on Discord side.")
                    self.update_bot_status("058-error", "Not ready, no linked account!")
                    dialog = wx.MessageDialog(None, "Cannot log in. You have no linked account on the Discord side of the bot. "
                                                    "Please link your account to the bot first by using the 's!link' command.",
                                              "Error", wx.CLOSE | wx.ICON_ERROR)
                    dialog.ShowModal()
                    self.clear_spotify_client()
                    self.login_window.login_button.Enable()
                    return
                else:
                    print("Connection error with bot backend.")
                    self.update_bot_status("058-error", short_msg)
                    dialog = wx.MessageDialog(None, f"Cannot connect to the bot. {msg}\nPlease try again later.",
                                              "Error", wx.CLOSE | wx.ICON_ERROR)
                    dialog.ShowModal()
                    self.clear_spotify_client()
                    self.login_window.login_button.Enable()
                    return


            print("Connected to bot, linked account found. OK to connect!")
            self.log("Connected to bot, linked account found. OK to connect!")
            self.update_bot_status("061-info", "Ready, waiting for link code")

    def on_login_window_key_up(self, event):
        # If the enter key was pressed in the login dialog, while the focus is in one of the text boxes or the bitrate,
        # and both dialog boxes are filled with something, act as if the login button was pressed
        if event.GetKeyCode() == wx.WXK_RETURN:
            focus = self.login_window.FindFocus()
            if focus in [self.login_window.username, self.login_window.password, self.login_window.bitrate]:
                username, password = self.login_window.username.GetValue(), self.login_window.password.GetValue()
                if username and password:
                    self.on_login_clicked(event)
        else:
            # Skip event
            event.Skip()

    def on_status_window_key_up(self, event):
        # If the enter key was pressed in the status dialog, while the focus is in the link code text box,
        # we are not connected and the link code is filled in, then act as if the connect button was pressed.
        if event.GetKeyCode() == wx.WXK_RETURN:
            focus = self.status_window.FindFocus()
            if focus == self.status_window.link_code and self.status_window.connect_button.GetLabel() == "Connect":
                link_code = self.status_window.link_code.GetValue()
                if link_code:
                    self.on_connect_clicked(event)
        else:
            # Skip event
            event.Skip()

    def on_exit_clicked(self, event):
        with self.gui_update_lock:
            self.status_window.exit_button.Disable()
            self.status_window.Close()

    def on_about_clicked(self, event):
        with self.gui_update_lock:
            self.check_latest_version()
            self.about_window.Show()

    def on_about_close_clicked(self, event):
        with self.gui_update_lock:
            self.about_window.Hide()

    def on_logout_clicked(self, event):
        with self.gui_update_lock:
            self.status_window.log_out_button.Disable()
            # Close out any open connection and shut down the spotify controller
            self.clear_spotify_client()

            self.status_window.Hide()
            self.login_window.Show()

            # Reset status window elements to default state
            self.status_window.log_out_button.Enable()
            self.status_window.link_code.SetValue("")
            self.status_window.connect_button.SetLabel("Connect")
            self.update_spotify_status("060-warning", "Unknown")
            self.update_bot_status("060-warning", "Unknown")
            self.status_window.log_text.SetValue("")

    def on_minimize_clicked(self, event):
        with self.gui_update_lock:
            self.minimized = True
            if self.taskbar_icon is None:
                self.taskbar_icon = SpoofyTaskBarIcon(frame=self)
            self.status_window.Hide()

    def on_taskbar_restore(self):
        with self.gui_update_lock:
            if self.taskbar_icon is not None:
                self.taskbar_icon.RemoveIcon()
                self.taskbar_icon.Destroy()
                self.taskbar_icon = None
            self.status_window.Show()
            self.status_window.Restore()
            self.minimized = False

    def on_connect_clicked(self, event):
        with self.gui_update_lock:
            self.status_window.connect_button.Disable()
            label = self.status_window.connect_button.GetLabel()

            if label == "Connect":
                self.log("Connecting to the bot...")
                link_code = self.status_window.link_code.GetValue()
                if link_code and self.spotify_client:
                    done, msg_or_addr, short_msg_or_port = self.spotify_client.connect_req(self.username, link_code)
                    if not done:
                        self.update_bot_status("058-error", f"{short_msg_or_port}")
                        self.log(f"[ERROR] {msg_or_addr}")
                        self.status_window.connect_button.Enable()
                        return

                    address, port = msg_or_addr, short_msg_or_port
                    self.spotify_client.address, self.spotify_client.port = address, port
                    self.spotify_client.setup_output_thread()
                    res, msg, short_msg = self.spotify_client.start_req(link_code)
                    if res:
                        self.status_window.connect_button.SetLabel("Disconnect")
                        self.update_bot_status("059-success", f"Connected and streaming!")
                        self.log("Connected and streaming! You can now start using the bot.")
                    else:
                        self.update_bot_status("058-error", short_msg)
                        self.log(f"[ERROR] Error during connection. {msg}")
                        self.spotify_client.disconnect()

                elif not link_code:
                    self.update_bot_status("061-info", f"Ready, waiting for link code")
                    self.log(f"[ERROR] No link code given.")
                else:
                    self.update_spotify_status("058-error", f"Spotify client is not running.")
                    self.log(f"[ERROR] Spotify client is not running. Please log out and log back in.")

            elif label == "Disconnect":
                self.log("Disconnecting from the bot...")
                self.update_bot_status("061-info", f"Disconnecting...")
                self.spotify_client.disconnect()
                self.status_window.connect_button.SetLabel("Connect")
                self.log("Disconnected.")
                self.update_bot_status("060-warning", f"Disconnected.")

            self.status_window.connect_button.Enable()

    def on_login_window_close(self, event):
        with self.gui_update_lock:
            print("Quitting")
            if self.spotify_client is not None:
                self.spotify_client.stop()
            self.ExitMainLoop()

    def on_status_window_close(self, event):
        with self.gui_update_lock:
            self.clear_spotify_client()
            print("Quitting")
            self.ExitMainLoop()

    def on_bot_disconnect(self):
        disconnect_evt = BotEvent(evt_type="disconnect")
        wx.PostEvent(self, disconnect_evt)

    # Handle incoming log message events
    def on_log_event(self, event: LogEvent):
        with self.gui_update_lock:
            self.log(event.msg)

    # Handle errors from LibreSpot
    def on_spotify_event(self, event: SpotifyEvent):
        with self.gui_update_lock:
            if event.evt_type == "auth_error":
                print(f"Spotify auth error: {event.err_msg}")
                self.update_spotify_status("058-error", f"{event.err_msg}")
                if "premium" in event.err_msg.lower():
                    dialog_msg = "Cannot log in. You need to have a Spotify Premium account to use this bot."
                else:
                    dialog_msg = "Cannot log in. Your Spotify username and password were incorrect. "\
                                 "Please use valid credentials."
                dialog = wx.MessageDialog(None, dialog_msg,
                                          "Error", wx.CLOSE | wx.ICON_ERROR)
                dialog.ShowModal()
                self.clear_spotify_client()
                self.login_window.login_button.Enable()
            elif event.evt_type == "auth_success":
                print(f"Spotify auth success, authenticated as {event.username}")
                # Set spotify status to green, add username to message.
                self.update_spotify_status("059-success", f"Connected to account '{event.username}'.")
                # Hide login window, show main window
                self.login_window.Hide()
                self.status_window.Show()
                self.login_window.login_button.Enable()

    # Handle bot events
    def on_bot_event(self, event: BotEvent):
        with self.gui_update_lock:
            if event.evt_type == "disconnect":
                self.log("Bot has disconnected from voice, or there are connection problems...")
                self.update_bot_status("061-info", f"Disconnecting...")
                if self.spotify_client is not None:
                    self.spotify_client.disconnect()
                self.status_window.connect_button.SetLabel("Connect")
                self.log("Disconnected.")
                self.update_bot_status("060-warning", f"Disconnected.")
