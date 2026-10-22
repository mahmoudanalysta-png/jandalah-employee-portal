# Jandalah Employee Portal Mobile App (Capacitor)

This folder contains the mobile wrapper configuration for **Jandalah Employee Self-Service Portal** using **Capacitor 6**.

## Prerequisites
- **Node.js**: v18+ and `npm`
- **Android Studio**: Installed with Android SDK 34+ (for Android builds)
- **Xcode**: Installed on macOS (for iOS builds)

## Setup Instructions

1. **Install dependencies:**
   ```bash
   npm install
   ```

2. **Add Android platform:**
   ```bash
   npx cap add android
   ```

3. **Add iOS platform (macOS only):**
   ```bash
   npx cap add ios
   ```

4. **Sync web & native configurations:**
   ```bash
   npx cap sync
   ```

5. **Open in Android Studio:**
   ```bash
   npx cap open android
   ```
   - From Android Studio: Build -> Generate Signed Bundle / APK -> Android App Bundle (`.aab`) for Google Play.

6. **Open in Xcode (macOS only):**
   ```bash
   npx cap open ios
   ```
   - From Xcode: Select Target -> Signing & Capabilities (select Team) -> Product -> Archive -> Distribute App -> App Store Connect.

## Server URL Configuration
Open `capacitor.config.json` and adjust the production portal URL:
```json
{
  "server": {
    "url": "https://portal.jandalah.com.sa"
  }
}
```
