// Run with node tests/client_login.cjs [path/to/main.py]. No browser or network required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

const mainPath = process.argv[2] || path.join(__dirname, "..", "main.py");
// The server fills the page's length rule from member_auth.MIN_PASSWORD_CHARS.
const minChars = fs.readFileSync(path.join(path.dirname(mainPath), "member_auth.py"), "utf8")
    .match(/^MIN_PASSWORD_CHARS = (\d+)$/m)[1];
const source = fs.readFileSync(mainPath, "utf8").replaceAll("__MIN_PASSWORD_CHARS__", minChars);
// The server fills the signup switch from GOGOGO_SIGNUP_ROOM_PASSWORD (off by default).
const scriptsFor = signupRoom => Array.from(source.replaceAll("__SIGNUP_ROOM_PASSWORD__", signupRoom ? "1" : "0")
    .matchAll(/<script>([\s\S]*?)<\/script>/g), match => match[1]);
const password = "synthetic test password";

function page(countResponse = () => ({ok: true, json: async () => ({count: 0})}), {signupRoom = false} = {}) {
    const scripts = scriptsFor(signupRoom);
    const elements = new Map();
    const storage = new Map();
    const sockets = [];
    const requests = [];
    const notices = [];
    const events = [];
    const intervals = new Map();
    const timeouts = new Map();

    function el(id) {
        if (!elements.has(id)) {
            elements.set(id, {value: "", style: {}, classList: {toggle() {}}, addEventListener() {}, focus() {}});
        }
        return elements.get(id);
    }

    class Socket {
        static OPEN = 1;
        constructor() { this.sent = []; this.readyState = 0; sockets.push(this); }
        send(body) { this.sent.push(JSON.parse(body)); }
        close() { this.readyState = 3; }
        open() { this.readyState = 1; this.onopen(); }
        drop(code) { this.readyState = 3; this.onclose?.({code}); }
        async receive(packet) { await this.onmessage({data: JSON.stringify(packet)}); }
    }

    const context = vm.createContext({
        console, Date, Math, JSON, Intl, URL, URLSearchParams,
        document: {
            getElementById: el, querySelector: () => null,
            addEventListener() {}, documentElement: {style: {setProperty() {}}}
        },
        localStorage: {
            setItem(k, v) { storage.set(k, v); }, getItem(k) { return storage.get(k) || null; },
            removeItem(k) { storage.delete(k); }
        },
        location: {protocol: "https:", host: "test.invalid", reload() { events.push("reload"); }},
        WebSocket: Socket,
        fetch: async (url, options) => {
            requests.push({url, options});
            if (url === "/user_count") return countResponse();
            return {ok: true, json: async () => ({})};
        },
        setTimeout(callback, delay) {
            const id = Symbol();
            timeouts.set(id, {callback, delay});
            return id;
        },
        clearTimeout(id) { timeouts.delete(id); },
        setInterval(callback, delay) {
            const id = Symbol();
            intervals.set(id, {callback, delay});
            return id;
        },
        clearInterval(id) { intervals.delete(id); },
        alert: value => notices.push(value), addEventListener() {}
    });
    context.window = context;
    const run = source => vm.runInContext(source, context);
    for (const script of scripts) run(script);
    // The application's rendering and media devices are independent of the login form.
    context.recordEvent = value => events.push(value);
    run('initCards = () => recordEvent("cards"); loadLocalBackground = () => recordEvent("background");');
    function submit(mode, nickname, pw, options = {}) {
        if (mode !== "migrate") run("setLoginMode(" + JSON.stringify(mode) + ")");
        el("nickInput").value = nickname;
        el("pwInput").value = pw;
        el("confirmInput").value = options.confirm ?? pw;
        el("roomInput").value = options.room || "";
        el("recoveryInput").value = options.code || "";
        run("login()");
        const socket = sockets.at(-1);
        if (socket?.readyState === 0) socket.open();
        return socket;
    }
    function runReconnectTimers() {
        for (const [id, timer] of [...timeouts]) {
            if (timer.delay === 3000) {
                timeouts.delete(id);
                timer.callback();
            }
        }
    }
    return {el, storage, sockets, requests, notices, events, intervals, run, submit, runReconnectTimers};
}

test("login screen polls only the public count, shows unknown on error, and stops after entry", async () => {
    let reply = () => ({ok: true, json: async () => ({count: 2})});
    const p = page(() => reply());
    const afterFetch = () => new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(p.requests.map(r => r.url), ["/user_count"]);
    assert.equal(p.requests[0].options.headers, undefined);
    await afterFetch();
    const text = () => p.el("loginUserCount").textContent;
    assert.equal(text(), "🔥 현재 달리고 있는 작가님: 2명");
    const [id, timer] = [...p.intervals].find(([, item]) => item.delay === 10000);
    for (const invalid of [
        () => { throw Error("offline"); },
        () => ({ok: false}),
        () => ({ok: true, json: async () => ({count: "private"})})
    ]) {
        reply = invalid;
        timer.callback();
        await afterFetch();
        assert.equal(text(), "🔥 현재 달리고 있는 작가님: ?명");
    }
    reply = () => ({ok: true, json: async () => ({count: 0})});
    timer.callback();
    await afterFetch();
    assert.equal(text(), "🔥 현재 달리고 있는 작가님: 0명");
    const socket = p.submit("login", "writer", password);
    await socket.receive({type: "auth_ok", is_admin: false, session: "test-token"});
    const count = p.requests.length;
    timer.callback();
    assert.equal(p.requests.length, count);
    assert.ok(!p.intervals.has(id));
});

test("a new browser needs only nickname and its personal password; room starts after auth", async () => {
    const p = page();
    const socket = p.submit("login", "writer", password);
    assert.deepEqual(socket.sent, [{type: "set_nickname", auth_mode: "login", nickname: "writer", password, owned: []}]);
    await socket.receive({type: "welcome", clientId: "test"});
    await socket.receive({type: "user_list", users: [{nickname: "private"}], count: 1});
    assert.equal(p.el("userCount").innerText, undefined);
    assert.deepEqual(p.requests.map(r => r.url), ["/user_count"]);
    assert.deepEqual(p.events, []);
    await socket.receive({type: "auth_ok", is_admin: false, session: "test-token"});
    assert.equal(p.el("loginOverlay").style.display, "none");
    assert.deepEqual(p.events, ["cards", "background"]);
    assert.equal(p.requests.find(r => r.url === "/pomo-config").options.headers["X-Gogogo-Session"], "test-token");
    assert.equal(p.el("pwInput").value, "");
    assert.ok(!Array.from(p.storage.values()).includes(password));
});

test("legacy setup uses verified browser and room proof; reconnect uses personal login", async () => {
    const p = page();
    p.storage.set("gogogoIdentity:writer", "synthetic-device-key");
    const legacy = p.submit("legacy", "writer", "test-room");
    assert.equal(legacy.sent[0].identity_key, "synthetic-device-key");
    assert.deepEqual(p.requests.map(r => r.url), ["/user_count"]);
    await legacy.receive({type: "auth_failed", reason: "setup_required"});
    assert.equal(p.el("loginMode").value, "migrate");
    const migration = p.submit("migrate", "writer", password);
    assert.deepEqual(migration.sent[0], {type: "set_nickname", auth_mode: "migrate", nickname: "writer", password,
        room_password: "test-room", identity_key: "synthetic-device-key", owned: []});
    await migration.receive({type: "auth_ok", is_admin: false, session: "test-token"});
    assert.equal(p.storage.has("gogogoIdentity:writer"), false);
    p.run("connectWebSocket()");
    const reconnect = p.sockets.at(-1);
    reconnect.open();
    assert.deepEqual(reconnect.sent[0], {type: "set_nickname", auth_mode: "login", nickname: "writer", password, owned: []});
});

test("signup and recovery submit only their own proof and do not reuse it after success", async () => {
    for (const [mode, signupRoom] of [["signup", false], ["signup", true], ["recover", false]]) {
        const p = page(undefined, {signupRoom});
        assert.equal(p.el("roomInput").hidden, true);
        p.run(`setLoginMode("${mode}")`);
        assert.equal(p.el("roomInput").hidden, !(mode === "signup" && signupRoom));
        const socket = p.submit(mode, "writer", password, {room: "test-room", code: "test-code"});
        // Default signup needs only the address: no room password or code from the admin.
        const proof = mode === "recover" ? {enroll_code: "test-code"} : signupRoom ? {room_password: "test-room"} : {};
        assert.deepEqual(socket.sent[0], {type: "set_nickname", auth_mode: mode, nickname: "writer", password, ...proof, owned: []});
        await socket.receive({type: "auth_ok", is_admin: false, session: "test-token"});
        p.run("connectWebSocket()");
        const reconnect = p.sockets.at(-1); reconnect.open();
        assert.deepEqual(reconnect.sent[0], {type: "set_nickname", auth_mode: "login", nickname: "writer", password, owned: []});
    }
});

test("invalid credentials and a missing legacy key stay at login; admin UI waits for server", async () => {
    const p = page();
    p.submit("legacy", "writer", "test-room");
    assert.equal(p.sockets.length, 0);
    const socket = p.submit("login", "부엉", "test-admin");
    assert.equal(socket.sent[0].auth_mode, "admin");
    assert.equal(p.el("adminLogBtn").style.display, undefined);
    await socket.receive({type: "auth_failed", reason: "password"});
    assert.equal(p.el("loginOverlay").style.display, "flex");
    assert.equal(p.el("loginSubmit").disabled, false);
    assert.deepEqual(p.requests.map(r => r.url), ["/user_count"]);
    assert.deepEqual(p.events, []);
});

test("new and confirmation use NFC code points; raw login, room and admin proofs are preserved", async () => {
    const p = page(undefined, {signupRoom: true});
    const composed = "  열다섯 글자가 넘는 개인 비밀번호  ";
    const raw = composed.normalize("NFD");
    assert.notEqual(raw, composed);
    // The decomposed spelling of these phrases must not bypass the length policy.
    for (const invalid of ["가나다라마바사", "가".repeat(Number(minChars) - 1), "w".repeat(129)]) {
        p.submit("signup", "writer", invalid.normalize("NFD"), {room: "invite"});
        assert.equal(p.sockets.length, 0);
    }
    const signup = p.submit("signup", "writer", raw, {confirm: composed, room: raw});
    assert.equal(signup.sent[0].password, composed);
    assert.equal(signup.sent[0].room_password, raw);
    const admin = page().submit("login", "부엉".normalize("NFD"), raw);
    assert.equal(admin.sent[0].auth_mode, "admin");
    assert.equal(admin.sent[0].password, raw);
    const legacyPage = page();
    legacyPage.storage.set("gogogoIdentity:writer", "test-key");
    const legacy = legacyPage.submit("legacy", "writer", raw);
    assert.equal(legacy.sent[0].password, raw);
    const login = page().submit("login", "writer", raw);
    assert.equal(login.sent[0].password, raw);
});

test("passwords are masked by default and remasked when switching modes or submitting", () => {
    const p = page();
    assert.equal(p.el("pwInput").type, "password");
    p.run("showPasswords('login')");
    assert.equal(p.el("pwInput").type, "text");
    p.run("setLoginMode('recover')");
    assert.equal(p.el("pwInput").type, "password");
    p.run("showPasswords('login')");
    p.el("nickInput").value = "writer";
    p.el("pwInput").value = p.el("confirmInput").value = password;
    p.el("recoveryInput").value = "test-code";
    p.run("login()");
    assert.equal(p.el("pwInput").type, "password");
    assert.equal(p.el("loginVisibility").ariaPressed, "false");
});

test("ambiguous signup, migration and recovery direct the user to normal login with the new password", async () => {
    for (const mode of ["signup", "migrate", "recover"]) {
        const p = page();
        if (mode === "migrate") {
            p.storage.set("gogogoIdentity:writer", "test-key");
            const proof = p.submit("legacy", "writer", "test-room");
            await proof.receive({type: "auth_failed", reason: "setup_required"});
        }
        const socket = p.submit(mode, "writer", password, {room: "test-room", code: "test-code"});
        await socket.receive({type: "auth_failed", reason: "storage"});
        assert.equal(p.el("loginMode").value, "login");
        assert.match(p.el("loginStatus").textContent, /방금 정한 새 개인 비밀번호/);
        p.runReconnectTimers();
        assert.equal(p.sockets.at(-1), socket);
        assert.deepEqual(p.requests.map(r => r.url), ["/user_count"]);
    }
});

test("dedicated close code without a notice and failed auth stop reconnection", async () => {
    for (const scenario of ["close only", "denied", "denied close only"]) {
        const p = page();
        const socket = p.submit("login", "writer", password);
        if (scenario.startsWith("denied")) {
            if (scenario === "denied") await socket.receive({type: "auth_failed", reason: "credentials"});
        } else {
            await socket.receive({type: "auth_ok", is_admin: false, session: "test-token"});
        }
        socket.drop(4001);
        p.runReconnectTimers();
        assert.equal(p.sockets.length, 1);
        assert.equal(p.run("authRequest"), null);
        assert.equal(p.run("sessionToken"), "");
    }
});

test("network loss retries a previously authenticated login once; a rejected retry stops", async () => {
    const p = page();
    const socket = p.submit("login", "writer", password);
    await socket.receive({type: "auth_ok", is_admin: false, session: "test-token"});
    socket.drop(1006);
    p.runReconnectTimers();
    assert.equal(p.sockets.length, 2);
    const next = p.sockets[1];
    next.open();
    assert.deepEqual(next.sent, socket.sent);
    await next.receive({type: "auth_failed", reason: "credentials"});
    next.drop(4001);
    p.runReconnectTimers();
    assert.equal(p.sockets.length, 2);
    assert.equal(p.run("authRequest"), null);
});

test("after entering the room, any close code except 4001 reconnects with the same login", async () => {
    for (const code of [1000, 1001, 1005, 1006, 1011, 1012, 1013]) {
        const p = page();
        const socket = p.submit("login", "writer", password);
        await socket.receive({type: "auth_ok", is_admin: false, session: "test-token"});
        socket.drop(code);
        p.runReconnectTimers();
        assert.equal(p.sockets.length, 2, `code ${code}`);
        const next = p.sockets[1];
        next.open();
        assert.deepEqual(next.sent, socket.sent, `code ${code}`);
    }
});

test("a signed-in member changes the password without the current one; reconnects use the new one", async () => {
    const p = page();
    const socket = p.submit("login", "writer", password);
    await socket.receive({type: "auth_ok", is_admin: false, session: "test-token"});
    assert.equal(p.el("changePasswordBtn").hidden, false);
    p.el("newPassword").value = "short";
    p.el("confirmPassword").value = "short";
    p.run("changePassword()");
    assert.equal(socket.sent.length, 1);
    const next = "a new password set by the member";
    p.el("newPassword").value = next;
    p.el("confirmPassword").value = next;
    p.run("changePassword()");
    assert.deepEqual(socket.sent.at(-1), {type: "change_password", new_password: next});
    assert.equal(p.el("newPassword").value, "");
    await socket.receive({type: "password_changed", ok: true, reason: null});
    assert.equal(p.sockets.length, 1);
    p.run("connectWebSocket()");
    const reconnect = p.sockets.at(-1); reconnect.open();
    assert.equal(reconnect.sent[0].password, next);
});

test("the admin switches the signup room password with one checkbox", async () => {
    const p = page();
    const socket = p.submit("login", "부엉", "test-admin");
    await socket.receive({type: "auth_ok", is_admin: true, session: "test-token"});
    assert.equal(p.el("signupRoomToggle").checked, false);
    p.run("setSignupRoomPassword(true)");
    assert.deepEqual(socket.sent.at(-1), {type: "set_signup_room_password", on: true});
    await socket.receive({type: "signup_room_password", ok: true, on: true});
    assert.equal(p.el("signupRoomToggle").checked, true);
    assert.equal(p.notices.length, 0);
    // A failed save shows the state the server actually kept.
    p.run("setSignupRoomPassword(false)");
    await socket.receive({type: "signup_room_password", ok: false, reason: "storage", on: true});
    assert.equal(p.el("signupRoomToggle").checked, true);
    assert.equal(p.notices.length, 1);
    assert.match(p.notices[0], /저장하지 못했습니다/);
});
