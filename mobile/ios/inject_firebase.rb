# Run once per CI build, before `pod install`/archive (see ../../codemagic.yaml).
#
# Why this exists: dropping GoogleService-Info.plist into ios/App/App/ is not
# enough on its own -- Xcode only bundles a file into the app if the .xcodeproj
# has a reference to it in the target's "Copy Bundle Resources" build phase,
# and that reference lives in project.pbxproj, which nothing here edits
# automatically the way Android's Gradle plugin auto-detects google-services.json.
# Normally you'd add it once by hand in Xcode -- not an option with no local Mac
# (see MOBILE_SETUP.md), so this script does the same edit with the `xcodeproj`
# gem instead, idempotently, on every build, so a fresh checkout always ends up
# correctly wired with no manual Xcode step, ever.
#
# Also adds App.entitlements (Push Notifications capability -- aps-environment)
# the same way, since a fresh `cap add ios` doesn't generate one either.
#
# Usage: ruby inject_firebase.rb   (run from mobile/ios/, i.e. this file's dir)
require 'xcodeproj'
require 'fileutils'

PROJECT_PATH = 'App/App.xcodeproj'
TARGET_NAME = 'App'
PLIST_NAME = 'GoogleService-Info.plist'
ENTITLEMENTS_NAME = 'App.entitlements'

project = Xcodeproj::Project.open(PROJECT_PATH)
target = project.targets.find { |t| t.name == TARGET_NAME } or abort("target #{TARGET_NAME} not found")
group = project.main_group.find_subpath('App', true)

# --- GoogleService-Info.plist -> Copy Bundle Resources -----------------
plist_path = "App/#{PLIST_NAME}"
if File.exist?(plist_path)
  already = target.resources_build_phase.files.any? { |f| f.file_ref && f.file_ref.path == PLIST_NAME }
  unless already
    ref = group.new_reference(PLIST_NAME)
    target.resources_build_phase.add_file_reference(ref)
    puts "added #{PLIST_NAME} to Copy Bundle Resources"
  end
else
  puts "warning: #{plist_path} not found -- push notifications will not work on iOS (see MOBILE_SETUP.md)"
end

# --- App.entitlements (Push Notifications capability) -------------------
entitlements_path = "App/#{ENTITLEMENTS_NAME}"
unless File.exist?(entitlements_path)
  File.write(entitlements_path, <<~PLIST)
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
    \t<key>aps-environment</key>
    \t<string>production</string>
    </dict>
    </plist>
  PLIST
  puts "created #{entitlements_path}"
end
unless target.resources_build_phase.files.any? { |f| f.file_ref && f.file_ref.path == ENTITLEMENTS_NAME }
  group.new_reference(ENTITLEMENTS_NAME) unless group.files.any? { |f| f.path == ENTITLEMENTS_NAME }
end
target.build_configurations.each do |config|
  config.build_settings['CODE_SIGN_ENTITLEMENTS'] = "App/#{ENTITLEMENTS_NAME}"
end

project.save
puts 'inject_firebase.rb done'
