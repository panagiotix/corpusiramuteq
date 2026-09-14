# Running the IRaMuTeQ Toolkit with Docker

This guide is for people with no Docker experience. It walks through
installing Docker, getting the project from GitHub, and running it on
**Windows**, **Mac**, or **Linux**. Once it's running, you use it the same
way on every system: open a web browser and go to
**http://localhost:8501**.

The project lives at:
**https://github.com/panagiotix/corpusiramuteq**

---

## Part 1 — Install Docker

You only need to do this once.

### Windows

1. Go to <https://www.docker.com/products/docker-desktop/> and download
   **Docker Desktop for Windows**.
2. Run the installer. When asked, leave **"Use WSL 2 instead of
   Hyper-V"** checked (it's the default) and let it finish.
3. Restart your computer if it asks you to.
4. Open **Docker Desktop** from the Start menu and wait for it to say
   it's running (a whale icon appears in the system tray, bottom-right).
   **Docker Desktop must be open and running** every time you want to
   use the app — if it's closed, the commands below won't work.
5. Open **PowerShell** (search for it in the Start menu) — this is
   where you'll type the commands in Part 2 and 3.

### Mac

1. Go to <https://www.docker.com/products/docker-desktop/> and download
   **Docker Desktop for Mac**.
   - If your Mac has an Apple Silicon chip (M1, M2, M3, M4 — most Macs
     from late 2020 onward), pick the **Apple Silicon** version.
   - If it's an older Intel Mac, pick the **Intel chip** version.
   - Not sure which you have? Apple menu → **About This Mac** — it
     tells you the chip.
2. Open the downloaded file and drag Docker into Applications, as
   instructed.
3. Open **Docker** from Applications and approve any permission
   prompts. Wait for the whale icon in the top menu bar to show Docker
   is running.
   **Docker must be open and running** every time you want to use the
   app.
4. Open **Terminal** (search for it with Spotlight, Cmd+Space) — this
   is where you'll type the commands in Part 2 and 3.

### Linux (Ubuntu/Debian example)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER   # optional: lets you skip "sudo" below — log out/in after this
```

### Check the install worked (any system)

Type this and press Enter:

```
docker --version
```

You should see a version number, e.g. `Docker version 27.x.x`.

---

## Part 2 — Get the project from GitHub

You have two options. If you're not sure, option A is simpler long-term
(it makes future updates a one-line command), but option B needs
nothing extra installed.

### Option A — with Git (recommended)

If you don't have Git yet:
- **Windows:** download it from <https://git-scm.com/download/win> and
  install with the default options.
- **Mac:** open Terminal and type `git --version` — if it's not
  installed, macOS will offer to install it for you (via Xcode Command
  Line Tools). Accept and wait for it to finish.
- **Linux:** `sudo apt-get install -y git`

Then, in your terminal (PowerShell on Windows, Terminal on Mac/Linux),
go to wherever you want the project folder to live (e.g. your Desktop)
and run:

```bash
cd Desktop
git clone https://github.com/panagiotix/corpusiramuteq.git
cd corpusiramuteq
```

You now have a `corpusiramuteq` folder with all the project files in it,
and your terminal is already inside it — ready for Part 3.

### Option B — without Git (download ZIP)

1. Go to <https://github.com/panagiotix/corpusiramuteq>.
2. Click the green **Code** button → **Download ZIP**.
3. Find the downloaded ZIP (usually in your Downloads folder) and
   extract/unzip it (right-click → *Extract All* on Windows,
   double-click on Mac).
4. Move the extracted `corpusiramuteq-main` folder wherever you like
   (e.g. your Desktop).
5. In your terminal, navigate into it, for example:

   **Windows (PowerShell):**
   ```powershell
   cd $HOME\Desktop\corpusiramuteq-main
   ```

   **Mac / Linux:**
   ```bash
   cd ~/Desktop/corpusiramuteq-main
   ```

---

## Part 3 — Build and run the app

From inside the project folder (see Part 2):

### Build the image (do this once, and again after any update)

```
docker build -t iramuteq-toolkit .
```

This downloads what it needs and installs everything inside the image.
The first time takes a couple of minutes; it's faster afterwards.

### Run it

```
docker run -d --name iramuteq-toolkit -p 8501:8501 --restart unless-stopped iramuteq-toolkit
```

- `-d` runs it in the background, so you get your terminal back.
- `--restart unless-stopped` makes it start automatically if Docker
  restarts, until you stop it yourself.

### Open the app

Open your web browser (Chrome, Edge, Safari, Firefox…) and go to:

```
http://localhost:8501
```

That's it — the app runs the same way regardless of Windows, Mac, or
Linux, because it's the same container underneath.

---

## Part 4 — Everyday use

```
# Stop the app
docker stop iramuteq-toolkit

# Start it again later (no need to rebuild)
docker start iramuteq-toolkit

# See what it's doing / check for errors
docker logs -f iramuteq-toolkit
```
(Press Ctrl+C to stop watching the logs — this does not stop the app.)

To fully remove it (e.g. before rebuilding a new version):
```
docker rm -f iramuteq-toolkit
```

---

## Part 5 — Updating to a newer version

**If you used Option A (Git):**

```bash
cd corpusiramuteq
git pull
docker stop iramuteq-toolkit
docker rm iramuteq-toolkit
docker build -t iramuteq-toolkit .
docker run -d --name iramuteq-toolkit -p 8501:8501 --restart unless-stopped iramuteq-toolkit
```

**If you used Option B (ZIP):** download the ZIP again from
<https://github.com/panagiotix/corpusiramuteq>, extract it over (or
alongside) the old folder, then run the same `docker stop` /
`docker rm` / `docker build` / `docker run` commands from inside it.

---

## Troubleshooting

**"docker: command not found" / "docker is not recognized"**
Docker Desktop (Windows/Mac) isn't installed or isn't running yet — open
it from the Start menu / Applications and wait for the whale icon to
settle, then try again.

**"Cannot connect to the Docker daemon"**
Same cause as above — Docker Desktop needs to be open and fully started
before you run any `docker` command.

**"git: command not found" / "git is not recognized"**
Git isn't installed — see Part 2, Option A for install links, or just
use Option B (download ZIP) instead, which needs nothing extra.

**"port is already allocated" / "address already in use"**
Something else on your machine is already using port 8501 (maybe the
app is already running). Check with `docker ps` — if `iramuteq-toolkit`
is already listed, it's already running; just open
http://localhost:8501. Otherwise, stop whatever else is using that
port, or run on a different port instead: replace `-p 8501:8501` with,
e.g., `-p 8502:8501`, and then visit http://localhost:8502.

**The browser shows "can't connect" / "refused to connect"**
Give it a few seconds after `docker run` — the app takes a moment to
start. Then check `docker logs iramuteq-toolkit` for errors.

**Windows: Docker Desktop asks about WSL 2**
Accept the default and let it install WSL 2 if prompted — it's required
and Docker Desktop handles the setup automatically.

