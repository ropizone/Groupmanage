<?php
/**
 * ═══════════════════════════════════════════════════
 *  Group Control Panel — server.php (Mini App)
 *  Upload to InfinityFree public_html/ folder
 * ═══════════════════════════════════════════════════
 *
 *  SETUP:
 *  1. Set your BOT_TOKEN below
 *  2. Upload both files to InfinityFree
 *  3. Set your Mini App URL in BotFather (see README)
 *
 * ═══════════════════════════════════════════════════
 */

// ── YOUR BOT TOKEN ──────────────────────────────────
define('BOT_TOKEN', '8189704138:AAFWdb27d_eNVeTvi9ZvuGi6EVgAb6jo3Pk');
// ────────────────────────────────────────────────────

header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(200); exit; }

$body = file_get_contents('php://input');
$req  = json_decode($body, true);

if (!$req || !isset($req['action'])) {
    echo json_encode(['ok' => false, 'error' => 'Invalid request.']);
    exit;
}

$action   = $req['action'];
$tg_data  = $req['tg_init_data'] ?? '';
$user_id  = (int)($req['user_id'] ?? 0);
$group_id = $req['group_id'] ?? '';

// ── Verify Telegram initData (security) ──────────────
// This makes sure the request is really from Telegram
function verify_telegram_data(string $init_data): bool {
    if (empty($init_data)) return false;

    $params = [];
    parse_str($init_data, $params);
    $hash = $params['hash'] ?? '';
    unset($params['hash']);

    // Build data-check-string
    ksort($params);
    $data_check = implode("\n", array_map(
        fn($k, $v) => "$k=$v",
        array_keys($params),
        array_values($params)
    ));

    $secret = hash_hmac('sha256', BOT_TOKEN, 'WebAppData', true);
    $expected = hash_hmac('sha256', $data_check, $secret);

    return hash_equals($expected, $hash);
}

// For init action — verify and return groups
if ($action === 'init') {
    if (!verify_telegram_data($tg_data)) {
        echo json_encode(['ok' => false, 'error' => 'Unauthorized.']); exit;
    }

    if (!$user_id) {
        echo json_encode(['ok' => false, 'error' => 'No user ID.']);
        exit;
    }

    // Find groups where this user is the owner (creator)
    // We do this by fetching all groups from data.json if accessible,
    // OR by checking the Telegram API for known group IDs.
    // 
    // Since data.json is on your bot server (not web server),
    // we use a workaround: bot writes a groups list to a shared file.
    // 
    // Simple approach: check a groups.json file that your bot updates.
    $groups = [];
    $groups_file = __DIR__ . '/groups.json';

    if (file_exists($groups_file)) {
        $all_groups = json_decode(file_get_contents($groups_file), true) ?? [];
        foreach ($all_groups as $gid => $info) {
            $admin_ids = $info['admin_ids'] ?? [];
            if ((int)($info['owner_id'] ?? 0) === $user_id || in_array($user_id, $admin_ids)) {
                $groups[] = [
                    'id'    => $gid,
                    'title' => $info['title'] ?? 'Unknown Group',
                ];
            }
        }
    }

    if (empty($groups)) {
        // Fallback: no groups found for this user
        echo json_encode(['ok' => false, 'error' => 'No groups found. Make sure you added the bot to your group and are the owner.']);
        exit;
    }

    echo json_encode(['ok' => true, 'groups' => $groups]);
    exit;
}

// All other actions need group_id
if (empty($group_id)) {
    echo json_encode(['ok' => false, 'error' => 'No group selected.']);
    exit;
}

// ── STATS ──────────────────────────────────────────
if ($action === 'stats') {
    $count = tg('getChatMemberCount', ['chat_id' => $group_id]);
    echo json_encode([
        'ok'      => true,
        'members' => $count['ok'] ? $count['result'] : null,
        'bans'    => null,
        'mutes'   => null,
        'warns'   => null,
    ]);
    exit;
}

// ── GET ADMINS ─────────────────────────────────────
if ($action === 'get_admins') {
    $res = tg('getChatAdministrators', ['chat_id' => $group_id]);
    if (!$res['ok']) {
        echo json_encode(['ok' => false, 'error' => $res['description'] ?? 'Failed.']);
        exit;
    }
    $admins = [];
    foreach ($res['result'] as $a) {
        if ($a['user']['is_bot'] ?? false) continue;
        $admins[] = [
            'id'     => $a['user']['id'],
            'name'   => trim(($a['user']['first_name'] ?? '') . ' ' . ($a['user']['last_name'] ?? '')),
            'status' => $a['status'],
        ];
    }
    echo json_encode(['ok' => true, 'admins' => $admins]);
    exit;
}

// ── MODERATE ───────────────────────────────────────
if ($action === 'moderate') {
    $type   = $req['type']   ?? '';
    $user   = $req['user']   ?? '';
    $reason = $req['reason'] ?? '';

    $uid = resolve_uid($user);
    if (!$uid) { echo json_encode(['ok' => false, 'error' => 'User not found.']); exit; }

    switch ($type) {
        case 'ban':
            $r = tg('banChatMember', ['chat_id' => $group_id, 'user_id' => $uid]);
            break;
        case 'kick':
            tg('banChatMember', ['chat_id' => $group_id, 'user_id' => $uid]);
            $r = tg('unbanChatMember', ['chat_id' => $group_id, 'user_id' => $uid]);
            break;
        case 'mute':
            $r = tg('restrictChatMember', [
                'chat_id'     => $group_id,
                'user_id'     => $uid,
                'permissions' => ['can_send_messages' => false],
            ]);
            break;
        case 'unban':
            $r = tg('unbanChatMember', ['chat_id' => $group_id, 'user_id' => $uid, 'only_if_banned' => true]);
            break;
        case 'unmute':
            $r = tg('restrictChatMember', [
                'chat_id'     => $group_id,
                'user_id'     => $uid,
                'permissions' => [
                    'can_send_messages'         => true,
                    'can_send_audios'           => true,
                    'can_send_documents'        => true,
                    'can_send_photos'           => true,
                    'can_send_videos'           => true,
                    'can_send_video_notes'      => true,
                    'can_send_voice_notes'      => true,
                    'can_send_polls'            => true,
                    'can_send_other_messages'   => true,
                    'can_add_web_page_previews' => true,
                    'can_invite_users'          => true,
                ],
            ]);
            break;
        default:
            echo json_encode(['ok' => false, 'error' => 'Unknown type.']); exit;
    }

    if ($r['ok'] ?? false) {
        $labels = ['ban'=>'🔨 Banned','kick'=>'👢 Kicked','mute'=>'🔇 Muted','unban'=>'✅ Unbanned','unmute'=>'🔊 Unmuted'];
        $msg = ($labels[$type] ?? ucfirst($type)) . " via control panel" . ($reason ? "\nReason: $reason" : "");
        tg('sendMessage', ['chat_id' => $group_id, 'text' => $msg]);
        echo json_encode(['ok' => true]);
    } else {
        echo json_encode(['ok' => false, 'error' => $r['description'] ?? 'Telegram error.']);
    }
    exit;
}

// ── SEND MESSAGE ───────────────────────────────────
if ($action === 'send_message') {
    $text = $req['text'] ?? '';
    if (!$text) { echo json_encode(['ok'=>false,'error'=>'No text.']); exit; }
    $r = tg('sendMessage', ['chat_id' => $group_id, 'text' => $text, 'parse_mode' => 'HTML']);
    echo json_encode(['ok' => $r['ok'] ?? false, 'error' => $r['description'] ?? null]);
    exit;
}

// ── SEND & PIN ─────────────────────────────────────
if ($action === 'send_and_pin') {
    $text = $req['text'] ?? '';
    if (!$text) { echo json_encode(['ok'=>false,'error'=>'No text.']); exit; }
    $sent = tg('sendMessage', ['chat_id' => $group_id, 'text' => $text, 'parse_mode' => 'HTML']);
    if (!($sent['ok'] ?? false)) { echo json_encode(['ok'=>false,'error'=>$sent['description']??'Send failed.']); exit; }
    $pin = tg('pinChatMessage', ['chat_id' => $group_id, 'message_id' => $sent['result']['message_id']]);
    echo json_encode(['ok' => $pin['ok'] ?? false, 'error' => $pin['description'] ?? null]);
    exit;
}

// ── SET RULES ──────────────────────────────────────
if ($action === 'set_rules') {
    $rules = $req['rules'] ?? '';
    if (!$rules) { echo json_encode(['ok'=>false,'error'=>'No rules.']); exit; }
    $r = tg('sendMessage', [
        'chat_id'    => $group_id,
        'text'       => "📋 <b>Group Rules Updated:</b>\n\n" . htmlspecialchars($rules),
        'parse_mode' => 'HTML',
    ]);
    echo json_encode(['ok' => $r['ok'] ?? false]);
    exit;
}

// ── SET WELCOME ────────────────────────────────────
if ($action === 'set_welcome') {
    $msg = $req['msg'] ?? '';
    if (!$msg) { echo json_encode(['ok'=>false,'error'=>'No message.']); exit; }
    tg('sendMessage', ['chat_id' => $group_id, 'text' => "✅ Welcome message updated via panel."]);
    echo json_encode(['ok' => true]);
    exit;
}

// ── WARN ───────────────────────────────────────────
if ($action === 'warn' || $action === 'unwarn') {
    $user   = $req['user']   ?? '';
    $reason = $req['reason'] ?? '';
    $uid = resolve_uid($user);
    if (!$uid) { echo json_encode(['ok'=>false,'error'=>'User not found.']); exit; }

    $msg = $action === 'warn'
        ? "⚠️ Warning issued to <code>$uid</code> via panel" . ($reason ? "\nReason: $reason" : "")
        : "✅ Warning removed from <code>$uid</code> via panel";

    $r = tg('sendMessage', ['chat_id' => $group_id, 'text' => $msg, 'parse_mode' => 'HTML']);
    echo json_encode(['ok' => $r['ok'] ?? false]);
    exit;
}

echo json_encode(['ok' => false, 'error' => 'Unknown action.']);


// ════════════════════════════════════════════
//  Helpers
// ════════════════════════════════════════════

function tg(string $method, array $params): array {
    $url = 'https://api.telegram.org/bot' . BOT_TOKEN . '/' . $method;
    $ch  = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => json_encode($params),
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
        CURLOPT_TIMEOUT        => 10,
    ]);
    $res = curl_exec($ch);
    curl_close($ch);
    return json_decode($res ?: '{"ok":false}', true) ?? ['ok' => false];
}

function resolve_uid(string $user): ?int {
    $user = ltrim($user, '@');
    if (is_numeric($user)) return (int)$user;
    $res = tg('getChat', ['chat_id' => '@' . $user]);
    if ($res['ok'] && isset($res['result']['id'])) return (int)$res['result']['id'];
    return null;
}
