# Publishing Deep Sweep to the Play Store and App Store

The native app shell (`mobile/`) and its CI (`codemagic.yaml`) are built and
committed. What's left needs accounts and payment only you can set up --
nobody else can create these on your behalf. This is the exact order to do
it in, with no local Mac required anywhere (builds run on Codemagic's own
cloud Macs).

Total one-time cost: **$25** (Google, one-time) **+ $99/year** (Apple) **+**
whatever Codemagic tier you land on (free tier is 500 build minutes/month,
enough for occasional releases).

---

## 1. Firebase project (push notifications) -- do this first

Everything else references this.

1. [console.firebase.google.com](https://console.firebase.google.com) ->
   **Add project** -> name it (e.g. "Deep Sweep").
2. Add an **Android app**: package name `in.pkresearch.deepsweep` (must
   match `mobile/capacitor.config.json`'s `appId` exactly). Download
   **`google-services.json`** -- keep it, needed in step 5.
3. Add an **iOS app**: bundle ID `in.pkresearch.deepsweep`. Download
   **`GoogleService-Info.plist`** -- keep it, needed in step 5.
4. **Project settings -> Service accounts -> Generate new private key.**
   Downloads a JSON file. This is what the *server* (not the app) uses to
   send pushes -- see step 7.
5. **Project settings -> Cloud Messaging -> Apple app configuration ->
   APNs Authentication Key.** Upload a `.p8` key from your Apple Developer
   account (Certificates, Identifiers & Profiles -> Keys -> create one with
   "Apple Push Notifications service (APNs)" enabled, if you don't have one
   yet -- needs step 2's Apple Developer enrollment done first, so you may
   come back to this after step 2 below).

Keep all of these files somewhere safe outside the repo -- none of them get
committed (see `mobile/.gitignore`).

## 2. Apple Developer Program ($99/year)

1. [developer.apple.com/programs/enroll](https://developer.apple.com/programs/enroll) --
   needs an Apple ID, payment, and (for an individual) identity
   verification that can take a day or two.
2. Once approved: **Certificates, Identifiers & Profiles -> Identifiers ->
   +** -> register App ID `in.pkresearch.deepsweep`, with the **Push
   Notifications** capability checked.
3. **App Store Connect** ([appstoreconnect.apple.com](https://appstoreconnect.apple.com)) ->
   **My Apps -> +  -> New App**. Platform iOS, name "Deep Sweep", bundle ID
   the one you just registered, SKU anything (e.g. `deepsweep-ios`).
   This manual creation step can't be skipped -- the API can't create an
   app's very first App Store Connect record for you.
4. **Users and Access -> Integrations -> App Store Connect API -> +** --
   generate a key with **App Manager** role. Download the `.p8` **once**
   (Apple won't let you re-download it), and note the Key ID and Issuer ID.
   This is what lets Codemagic sign and upload with no local Mac -- see
   step 6.

## 3. Google Play Console ($25 one-time)

1. [play.google.com/console](https://play.google.com/console/signup) --
   pay the one-time $25 registration fee.
2. **Create app** -> name "Deep Sweep", default language English, App or
   game: App, Free.
3. Fill in the mandatory **App content** section: privacy policy URL
   (`https://deepsweep.pkresearch.in/privacy` -- already live, see
   `frontend/privacy.html`), content rating questionnaire (a finance/news
   app; answer honestly, likely lands on "Everyone" or "Teen"), target
   audience, **Data safety** form (declare: email/name collected for
   accounts, no data sold, no ad tracking -- see `frontend/privacy.html`
   for the exact list to transcribe).
4. **Setup -> API access** -> create/link a Google Cloud project, then
   **Service accounts -> create new service account**, grant it access
   under Play Console with permission to manage releases. Download its
   JSON key -- this is `$CM_GOOGLE_PLAY_SERVICE_ACCOUNT_CREDENTIALS` in
   `codemagic.yaml`.
5. Like Apple, the **very first release still has to be uploaded by hand**
   once (Play Console -> your app -> Testing -> Internal testing -> Create
   release -> upload an AAB) before the API can push further releases to
   it. Get one build out of Codemagic first (step 6) and upload that.

## 4. Codemagic account (CI -- builds both apps, no Mac needed)

1. [codemagic.io](https://codemagic.io) -> sign up, connect your GitHub
   account, add the `deep-microcap-screener` repo.
2. Codemagic auto-detects `codemagic.yaml` at the repo root.
3. **Team settings -> Environment variables** -> create a group named
   exactly `deepsweep_mobile` (matches `codemagic.yaml`), and add:
   - `GOOGLE_SERVICES_JSON_BASE64` -- run
     `base64 -w0 google-services.json` (from step 1.2) and paste the
     output as the value. Mark it **secret**.
   - `GOOGLE_SERVICE_INFO_PLIST_BASE64` -- same, from step 1.3's
     `GoogleService-Info.plist`. Mark it **secret**.
4. **Team settings -> Code signing identities -> Android keystore** ->
   upload a new keystore (Codemagic can generate one for you if you don't
   have one) named `deepsweep_keystore` (matches `codemagic.yaml`'s
   `android_signing`). **Back this up somewhere safe** -- losing it means
   you can never update the Play Store app again under the same listing.
5. **Team settings -> Integrations -> App Store Connect** -> add a new
   integration named `deepsweep_asc` (matches `codemagic.yaml`), using the
   Key ID / Issuer ID / `.p8` from step 2.4.
6. (Optional, once you've done the one manual Play Console upload in step
   3.5) **Integrations -> Google Play** -> add the service account JSON
   from step 3.4, then uncomment the `google_play:` publishing block in
   `codemagic.yaml`.

## 5. First build

Tag a commit to trigger both workflows (see `codemagic.yaml`'s comment on
why pushes to `main` don't trigger this automatically):

```
git tag mobile-v1.0.0
git push origin mobile-v1.0.0
```

Watch both workflows in the Codemagic dashboard. The Android one produces a
signed `.aab` (upload it by hand to Play Console's Internal testing track
the first time, per step 3.5); the iOS one uploads straight to TestFlight
(`submit_to_testflight: true`) once signing succeeds.

## 6. Store listing content (both stores need this regardless of CI)

- **Screenshots**: at least one phone screenshot per store, ideally 3-5
  showing the dashboard, Movers/Alerts, and News Channel. Easiest: run the
  app (or the website at phone width) and screenshot it.
- **Short/long description, app icon**: see `mobile/README.md` about
  replacing `mobile/resources/icon.png` with a crisp 1024x1024 source
  before this step -- the current one is upscaled from a 512x512 web icon
  and will look soft at full App Store/Play Store size.
- **Support URL / contact**: `researchpkinvestment@gmail.com` or
  `https://deepsweep.pkresearch.in/privacy` (has a contact email on it).
- **Apple's review notes**: since the app is login-gated, leave a demo
  account (email + password) in App Store Connect's "App Review
  Information -> Notes" field, or the reviewer can't get past `/login`
  and will reject it as "unable to test."

## 7. Turning on server-side push sending

Once step 1.4's Firebase service-account JSON exists, set it as an
environment variable on the **backend server itself** (not Codemagic --
this is the always-on app server, e.g. in `.env` or your host's secrets):

```
FIREBASE_CREDENTIALS_JSON={"type":"service_account", ...entire file contents on one line...}
```

Restart the server. `backend/app/push.py` picks it up automatically;
nothing else changes. Until this is set, the app and its push registration
endpoint work fine, they just never actually send anything (see
`push.configured()`).

## What ships automatically after this, and what doesn't

- **Dashboard content/features** (backend or frontend changes): ship the
  same way they always have -- no app store release involved, every
  existing install sees the change immediately (see `mobile/README.md`).
- **The native shell itself** (icons, push wiring, permissions,
  `capacitor.config.json`): needs a new version tag (`mobile-vX.Y.Z`) and
  goes through both stores' review again -- Google's is usually hours,
  Apple's 1-3 days typically.
- **This isn't fully hands-off**: Apple review can still reject a build
  for reasons unrelated to anything here (metadata, screenshots, a policy
  change), and the very first submission to each store needs the manual
  steps called out above (3 and 3.5/6.4). Budget for at least one round of
  back-and-forth on the first Apple submission -- it's normal.
