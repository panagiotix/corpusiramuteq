# Running the IRaMuTeQ Toolkit with Docker

This guide is for people with no Docker experience. It walks through
installing Docker and running the app on **Windows**, **Mac**, or **Linux**.
Once it's running, you use it the same way on every system: open a web
browser and go to **http://localhost:8501**.

## Files you need

Put these four files together in one folder (e.g. a folder called
`iramuteq-toolkit` on your Desktop):

- `Dockerfile`
- `.dockerignore`
- `app.py`
- `requirements.txt`

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
   where you'll type the commands in Part 2.

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
   is where you'll type the commands in Part 2.

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

## Part 2 — Build and run the app

Open a terminal (PowerShell on Windows, Terminal on Mac/Linux) and
navigate into the folder with the four files. For example, if it's on
your Desktop in a folder called `iramuteq-toolkit`:

**Windows (PowerShell):**
```powershell
cd $HOME\Desktop\iramuteq-toolkit
```

**Mac / Linux:**
```bash
cd ~/Desktop/iramuteq-toolkit
```

### Build the image (do this once, and again any time the files change)

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

## Part 3 — Everyday use

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

## Part 4 — Updating after the app is changed

If you get a new version of `app.py` (or any of the other three
files):

1. Replace the old file(s) in your folder with the new one(s).
2. Run:

```
docker stop iramuteq-toolkit
docker rm iramuteq-toolkit
docker build -t iramuteq-toolkit .
docker run -d --name iramuteq-toolkit -p 8501:8501 --restart unless-stopped iramuteq-toolkit
```

---

## Troubleshooting

**"docker: command not found" / "docker is not recognized"**
Docker Desktop (Windows/Mac) isn't installed or isn't running yet — open
it from the Start menu / Applications and wait for the whale icon to
settle, then try again.

**"Cannot connect to the Docker daemon"**
Same cause as above — Docker Desktop needs to be open and fully started
before you run any `docker` command.

**"port is already allocated" / "address already in use"**
Something else on your machine is already using port 8501 (maybe the
app is already running). Check with `docker ps` — if `iramuteq-toolkit`
is already listed, it's already running; just open
http://localhost:8501. Otherwise, stop whatever else is using that
port, or run on a different port instead: replace `-p 8501:8501` with,
e.g., `-p 8502:8501`, and then visit http://localhost:8502.

**The browser shows "can't connect" / "refused to connect"**
Give it a few seconds after `docker run` — Streamlit takes a moment to
start. Then check `docker logs iramuteq-toolkit` for errors.

**Windows: Docker Desktop asks about WSL 2**
Accept the default and let it install WSL 2 if prompted — it's required
and Docker Desktop handles the setup automatically.

## Is this really the same on Windows, Mac, and Linux?

Yes — Docker's whole point is that the app runs inside an identical
Linux environment regardless of your computer. Docker Desktop on
Windows and Mac transparently runs that Linux environment for you (via
WSL 2 on Windows, via Apple's virtualization on Mac, including native
support for Apple Silicon/M-series chips — no slow emulation needed).
The base image and all the Python packages this app uses (Streamlit,
requests, trafilatura, plotly, matplotlib) publish prebuilt versions
for both Intel/AMD and Apple Silicon/ARM processors, so the build step
doesn't need to compile anything from source on any of the three
systems. There are no Windows- or Mac-specific file paths anywhere in
the app or the Dockerfile, and (as of the current version) the app
doesn't require mounting any folder from your computer either — so
there's nothing platform-specific left to go wrong.

I reviewed the Dockerfile and every dependency for this — I was not
able to actually run a build on a Windows or Mac machine myself, so
this is a careful static check, not a live test. If anything goes
wrong on your machine, the Troubleshooting section above covers the
most likely causes; anything not covered there, paste me the exact
error and I'll help track it down.
