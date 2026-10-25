import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';

const user = {
  id: 'me',
  username: 'tester',
  display_name: 'Test Participant',
  avatar_color: '#10B981',
  default_input_lang: 'en',
  default_output_lang: 'hi',
};
const room = {
  id: 'room',
  room_code: 'kin-123-abc',
  title: 'Multilingual design review',
  host_id: 'me',
  is_host: true,
  status: 'live',
  is_active: true,
  record_session: true,
};

async function meeting(page: Page, initialView = 'dialogue', historyRooms = [room]) {
  let socket: WebSocketRoute | null = null;
  let microphoneFrames = 0;
  const sentMessages: unknown[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(
    ({ room, initialView }) => {
      localStorage.setItem('kinesia_auth_token', 'browser-test-token');
      localStorage.setItem('kinesia_view', initialView);
      localStorage.setItem('kinesia_room_code', room.room_code);
      sessionStorage.setItem('kinesia_join_muted', 'true');
    },
    { room, initialView }
  );
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    let data: unknown = {};
    if (url.endsWith('/auth/me')) data = user;
    else if (url.endsWith('/api/v1/rooms')) data = historyRooms;
    else if (url.endsWith('/moments')) data = [];
    else if (url.includes('/recap')) {
      const target = historyRooms.find((entry) => url.includes(entry.room_code)) || room;
      data = { status: 'not_generated', configured: true, language: 'hi', available: !target.is_active, conversation_active: target.is_active };
    }
    else if (url.endsWith('/transcripts')) data = [];
    else if (url.endsWith('/speak')) {
      sentMessages.push(route.request().postDataJSON());
      data = {};
    } else if (url.endsWith('/end'))
      data = {
        executive_summary: 'Recorded one test turn.',
        key_points: [],
        action_items: [],
        summary_method: 'extractive',
      };
    else data = room;
    await route.fulfill({ json: { ok: true, data, error: null } });
  });
  await page.routeWebSocket('**/ws/rooms/**', (connection) => {
    socket = connection;
    connection.onMessage((message) => {
      if (typeof message !== 'string') {
        microphoneFrames++;
        return;
      }
      const event = JSON.parse(message);
      if (event.type === 'authenticate') {
        connection.send(JSON.stringify({ type: 'connected', data: { room_code: room.room_code } }));
        connection.send(
          JSON.stringify({
            type: 'participants',
            data: [
              { user_id: 'me', user_name: user.display_name, speaking_lang: 'en', listening_lang: 'hi' },
              {
                user_id: 'peer',
                user_name: 'Guest Participant',
                speaking_lang: 'hi',
                listening_lang: 'en',
                avatar_color: '#06B6D4',
              },
            ],
          })
        );
      }
    });
  });
  await page.goto('/');
  if (initialView === 'dialogue') {
    await expect(page.getByRole('heading', { name: room.title })).toBeVisible();
    await expect(page.getByText('Live translation', { exact: true })).toBeVisible();
  } else await expect(page.getByRole('heading', { name: room.title, exact: true })).toBeVisible();
  return {
    send: (type: string, data: unknown) => socket!.send(JSON.stringify({ type, data })),
    frames: () => microphoneFrames,
    sentMessages,
    errors,
  };
}

test('create and passcode join forms have accessible controls and preserve recording choices', async ({ page }) => {
  const connection = await meeting(page, 'welcome');
  await page.getByRole('button', { name: 'Start a conversation', exact: true }).click();
  await page.getByLabel('Session title', { exact: true }).fill('Private chat');
  await page.getByLabel('Passcode (optional)', { exact: true }).fill('private-code');
  await page.getByRole('checkbox').uncheck();
  const request = page.waitForRequest((entry) => entry.method() === 'POST' && entry.url().endsWith('/api/v1/rooms'));
  await page.getByRole('button', { name: /Create/ }).click();
  expect((await request).postDataJSON()).toEqual({ title: 'Private chat', passcode: 'private-code', record_session: false });
  await expect(page.getByRole('button', { name: 'Enter Conversation', exact: true })).toBeVisible();
  expect(connection.errors).toEqual([]);
});

test('late profile refresh does not overwrite preferences being edited', async ({ page }) => {
  const connection = await meeting(page, 'welcome');
  let release!: () => void;
  let requested = false;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/v1/auth/me', async (route) => {
    requested = true;
    await pending;
    await route.fulfill({ json: { data: user } });
  });
  await page.getByRole('button', { name: 'Profile & Audio', exact: true }).click();
  await expect.poll(() => requested).toBe(true);
  await page.getByLabel('Display Name', { exact: true }).fill('My new display name');
  const response = page.waitForResponse((entry) => entry.url().endsWith('/auth/me'));
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByLabel('Display Name', { exact: true })).toHaveValue('My new display name');
  expect(connection.errors).toEqual([]);
});

test('a delayed history response cannot restore a cleared transcript', async ({ page }) => {
  const connection = await meeting(page);
  let release!: () => void;
  let requested = false;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/v1/rooms/*/transcripts', async (route) => {
    requested = true;
    await pending;
    await route.fulfill({ json: { data: [{ id: 'old-private-text', speaker_name: 'Guest', original_text: 'This text was cleared.', source_lang: 'en', translations: {} }] } });
  });
  await page.reload();
  await expect.poll(() => requested).toBe(true);
  connection.send('transcripts_cleared', {});
  const response = page.waitForResponse((entry) => entry.url().endsWith('/transcripts'));
  release();
  await (await response).finished();
  await page.getByRole('button', { name: /Transcript/ }).click();
  await expect(page.getByText('This text was cleared.', { exact: true })).toHaveCount(0);
  expect(connection.errors).toEqual([]);
});

test('meeting renders captions, deduplicates revised turns, and exports a transcript', async ({ page }) => {
  const connection = await meeting(page);
  await expect(page.getByLabel('Speaking language')).toHaveValue('en');
  await expect(page.getByLabel('Listening language')).toHaveValue('hi');
  await expect(page.getByRole('button', { name: 'Show original text' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Show original text' }).click();
  await expect(page.getByRole('button', { name: 'Show original text' })).toHaveAttribute('aria-pressed', 'false');
  await page.getByRole('button', { name: 'Show original text' }).click();
  const turn = {
    turn_id: 'one',
    revision: 1,
    speaker_id: 'peer',
    speaker_name: 'Guest Participant',
    source_lang: 'en',
    original_text: 'Hello everyone.',
    localized_text: 'सभी को नमस्कार।',
    listening_lang: 'hi',
  };
  connection.send('caption', turn);
  await expect(page.getByRole('status')).toContainText('सभी को नमस्कार');
  connection.send('turn', turn);
  connection.send('turn', turn);
  connection.send('turn', { ...turn, revision: 2 });
  await page.getByRole('button', { name: /Transcript/ }).click();
  await expect(page.getByRole('heading', { name: 'Conversation transcript' })).toBeVisible();
  await expect(page.getByText('सभी को नमस्कार।', { exact: true })).toHaveCount(2); // Caption plus one transcript item.
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('kinesia-kin-123-abc.txt');
  expect(connection.errors).toEqual([]);
  await page.screenshot({ path: 'test-results/meeting-desktop.png', fullPage: true });
});

test('microphone uses AudioWorklet frames and text fallback sends an idempotent request', async ({
  page,
}) => {
  const connection = await meeting(page);
  await page.getByRole('button', { name: 'Unmute', exact: true }).click();
  await expect.poll(connection.frames, { timeout: 15000 }).toBeGreaterThan(0);
  await page.getByLabel('Text message to translate').fill('Please send the notes tomorrow.');
  await page.getByRole('button', { name: 'Send and translate text', exact: false }).click();
  await expect.poll(() => connection.sentMessages.length).toBe(1);
  expect(connection.sentMessages[0]).toMatchObject({
    text: 'Please send the notes tomorrow.',
    source_lang: 'en',
    utterance_id: expect.any(String),
  });
  expect(connection.errors).toEqual([]);
});

test('mobile layout exposes language, microphone, transcript and recovery controls', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const connection = await meeting(page);
  await expect(page.getByLabel('Listening language')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Unmute', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End conversation', exact: true })).toBeVisible();
  await expect
    .poll(async () =>
      page.getByTestId('participant-card').evaluateAll((cards) => {
        const first = cards[0].getBoundingClientRect(),
          second = cards[1].getBoundingClientRect();
        return first.right <= second.left;
      })
    )
    .toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  connection.send('pipeline_error', {
    provider: 'cartesia',
    message: 'Translated audio is temporarily unavailable.',
  });
  await expect(page.getByRole('alert')).toContainText('temporarily unavailable');
  await expect(page.getByRole('button', { name: 'Reconnect audio' })).toBeVisible();
  const endControl = await page.getByRole('button', { name: 'End conversation', exact: true }).boundingBox();
  expect(endControl!.y + endControl!.height).toBeLessThanOrEqual(836);
  await expect.poll(() => page.getByTestId('participant-card').first().evaluate((card) => {
    const main = card.closest('main')!;
    return card.getBoundingClientRect().top >= main.getBoundingClientRect().top;
  })).toBe(true);
  await page.screenshot({ path: 'test-results/meeting-mobile.png', fullPage: true });
  expect(connection.errors).toEqual([]);
});

test('creator reopening a room from history keeps the End conversation control', async ({ page }) => {
  const connection = await meeting(page, 'history');
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await page.getByRole('button', { name: 'Re-join', exact: true }).click();
  await page.getByRole('button', { name: 'Enter Conversation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End conversation', exact: true })).toBeVisible();
  expect(connection.errors).toEqual([]);
});

test('history removal explains that other participants keep their copy', async ({ page }) => {
  const connection = await meeting(page, 'history');
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await expect(page.getByRole('button', { name: 'Remove from my history', exact: true })).toHaveAttribute('title', /other participants keep/);
  await page.getByRole('button', { name: 'Remove from my history', exact: true }).click();
  await expect(page.getByText('No sessions found', { exact: true })).toBeVisible();
  expect(connection.errors).toEqual([]);
});

test('a late history response cannot overwrite another selected conversation', async ({ page }) => {
  const ended = { ...room, is_active: false, status: 'ended' };
  const second = { ...ended, id: 'second', room_code: 'kin-456-def', title: 'Second conversation' };
  const connection = await meeting(page, 'history', [ended, second]);
  let release!: () => void;
  let firstRequested = false;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const ready = (text: string) => ({ status: 'ready', configured: true, language: 'hi', result: { summary: { text, evidence: text, source_ids: ['source'] }, highlights: [], follow_ups: [], open_questions: [] } });
  await page.route(/\/api\/v1\/rooms\/[^/]+\/recap(?:\?|$)/, async (route) => {
    if (route.request().url().includes(room.room_code)) {
      firstRequested = true;
      await gate;
      await route.fulfill({ json: { data: ready('Stale first conversation notes') } });
    } else await route.fulfill({ json: { data: ready('Correct second conversation notes') } });
  });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await expect.poll(() => firstRequested).toBe(true);
  await page.getByRole('heading', { name: second.title, exact: true }).click();
  await expect(page.getByText('Correct second conversation notes', { exact: true })).toBeVisible();
  const response = page.waitForResponse((result) => result.url().includes(`${room.room_code}/recap`));
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByText('Correct second conversation notes', { exact: true })).toBeVisible();
  await expect(page.getByText('Stale first conversation notes', { exact: true })).toHaveCount(0);
  expect(connection.errors).toEqual([]);
});

test('saved moments survive a reload and remain separate from the conversation recap', async ({ page }) => {
  const connection = await meeting(page);
  const turn = { id: 'remember-one', turn_id: 'remember-one', speaker_id: 'peer', speaker_name: 'Guest Participant', source_lang: 'en', listening_lang: 'hi', original_text: 'Remember tomorrow at six.', localized_text: 'कल छह बजे याद रखना।', translations: { hi: 'कल छह बजे याद रखना।' } };
  let saved = false;
  await page.route('**/api/v1/rooms/*/moments', async (route) => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataJSON()).toEqual({ turn_id: 'remember-one' });
      saved = true;
      await route.fulfill({ json: { data: { saved: true } } });
    } else await route.fulfill({ json: { data: saved ? [{ ...turn, room_code: room.room_code, title: room.title }] : [] } });
  });
  await page.route('**/api/v1/rooms/*/transcripts', (route) => route.fulfill({ json: { data: [turn] } }));
  connection.send('turn', turn);
  await page.getByRole('button', { name: /Transcript/ }).click();
  await page.getByRole('button', { name: 'Save moment', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Remove saved moment' })).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await expect(page.getByRole('heading', { name: room.title })).toBeVisible();
  await page.getByRole('button', { name: /Transcript/ }).click();
  await expect(page.getByRole('button', { name: 'Remove saved moment' })).toHaveAttribute('aria-pressed', 'true');
  expect(connection.errors).toEqual([]);
});

test('opening saved moments ignores an older conversation lookup arriving late', async ({ page }) => {
  const second = { ...room, room_code: 'second-moment', title: 'The second saved conversation' };
  const connection = await meeting(page, 'history', [room, second]);
  let release!: () => void;
  let firstRequested = false;
  let delayLookups = false;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/v1/moments', (route) => route.fulfill({ json: { data: [room, second].map((entry, index) => ({ turn_id: `saved-${index}`, room_code: entry.room_code, title: entry.title, speaker_name: 'Guest', original_text: 'A private bookmark.', translations: {} })) } }));
  await page.route(/\/api\/v1\/rooms\/[^/?]+$/, async (route) => {
    if (route.request().url().endsWith(room.room_code) && delayLookups) {
      firstRequested = true;
      await delayed;
      await route.fulfill({ json: { data: room } });
    } else await route.fulfill({ json: { data: route.request().url().endsWith(room.room_code) ? room : second } });
  });
  await page.reload();
  await page.getByRole('button', { name: 'Saved moments', exact: true }).click();
  delayLookups = true;
  await page.getByRole('button', { name: 'Open conversation', exact: true }).nth(0).click();
  await expect.poll(() => firstRequested).toBe(true);
  await page.getByRole('button', { name: 'Open conversation', exact: true }).nth(1).click();
  await expect(page.getByRole('heading', { name: second.title, exact: true })).toBeVisible();
  const response = page.waitForResponse((entry) => entry.url().endsWith(`/rooms/${room.room_code}`));
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByRole('heading', { name: second.title, exact: true })).toBeVisible();
  expect(connection.errors).toEqual([]);
});

test('review bookmarks update the private list and clearing their source removes them immediately', async ({ page }) => {
  const connection = await meeting(page, 'history');
  const turn = { id: 'private-source', turn_id: 'private-source', speaker_name: 'Guest', source_lang: 'en', original_text: 'Bring the blue notebook.', translations: {} };
  let saved = false;
  let cleared = false;
  const payload = () => saved && !cleared ? [{ ...turn, room_code: room.room_code, title: room.title }] : [];
  await page.route('**/api/v1/moments', (route) => route.fulfill({ json: { data: payload() } }));
  await page.route('**/api/v1/rooms/*/moments', async (route) => {
    if (route.request().method() === 'POST') saved = true;
    await route.fulfill({ json: { data: payload() } });
  });
  await page.route('**/api/v1/rooms/*/transcripts', async (route) => {
    if (route.request().method() === 'DELETE') cleared = true;
    await route.fulfill({ json: { data: cleared ? [] : [turn] } });
  });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await page.getByRole('button', { name: 'Save moment', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Remove saved moment', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Saved moments', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open conversation', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Clear shared transcript', exact: true }).click();
  await page.getByRole('button', { name: 'Clear for everyone', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open conversation', exact: true })).toHaveCount(0);
  expect(connection.errors).toEqual([]);
});

test('AI recap is requested explicitly in the review language and links to its source', async ({ page }) => {
  const ended = { ...room, is_active: false, status: 'ended' };
  const connection = await meeting(page, 'history', [ended]);
  const turn = { id: 'source-one', speaker_name: 'Guest Participant', source_lang: 'en', original_text: 'Tomorrow at six, not five.', localized_text: 'कल छह बजे, पाँच बजे नहीं।', translations: { hi: 'कल छह बजे, पाँच बजे नहीं।' } };
  let generated = false;
  const point = { text: 'हम कल छह बजे मिलेंगे, पाँच बजे नहीं।', source_ids: ['source-one'], evidence: turn.original_text };
  await page.route('**/api/v1/rooms/*/transcripts', (route) => route.fulfill({ json: { data: [turn] } }));
  await page.route(/\/api\/v1\/rooms\/[^/]+\/recap(?:\?|$)/, async (route) => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataJSON()).toEqual({ language: 'hi' });
      generated = true;
      await route.fulfill({ json: { data: { status: 'queued', configured: true } } });
    } else await route.fulfill({ json: { data: { status: generated ? 'ready' : 'not_generated', configured: true, language: 'hi', result: generated ? { summary: point, highlights: [], follow_ups: [], open_questions: [] } : undefined } } });
  });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await expect(page.getByRole('button', { name: 'Generate AI recap' })).toBeEnabled();
  expect(generated).toBe(false);
  await page.getByRole('button', { name: 'Generate AI recap' }).click();
  await expect(page.getByText(point.text, { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/conversation-recap.png', fullPage: true });
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export recap' }).click();
  expect((await download).suggestedFilename()).toBe(`kinesia-${room.room_code}-recap.md`);
  await page.getByRole('button', { name: 'Source 1', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Transcript', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText(turn.original_text, { exact: true })).toBeVisible();
  expect(connection.errors).toEqual([]);
  await page.screenshot({ path: 'test-results/conversation-history.png', fullPage: true });
});

test('overlapping speakers keep separate readable captions including an intentional hmm', async ({ page }) => {
  const connection = await meeting(page);
  connection.send('caption', { speaker_id: 'peer', speaker_name: 'Guest Participant', source_lang: 'en', listening_lang: 'hi', original_text: 'Hmm, yes.', localized_text: 'हम्म, हाँ।', audio_end_ms: 800 });
  connection.send('caption', { speaker_id: 'another', speaker_name: 'Another participant', source_lang: 'en', listening_lang: 'hi', original_text: 'I have another idea.', localized_text: 'मेरे पास एक और विचार है।', audio_end_ms: 1200 });
  await expect(page.getByRole('status')).toContainText('हम्म, हाँ।');
  await expect(page.getByRole('status')).toContainText('मेरे पास एक और विचार है।');
  connection.send('turn', { turn_id: 'old-final', speaker_id: 'another', speaker_name: 'Another participant', source_lang: 'en', listening_lang: 'hi', original_text: 'An older sentence.', localized_text: 'पुराना वाक्य।', audio_end_ms: 400 });
  await expect(page.getByRole('status')).not.toContainText('पुराना वाक्य।');
  expect(connection.errors).toEqual([]);
});

test('recap failure stays honest and can be retried without inventing notes', async ({ page }) => {
  const ended = { ...room, is_active: false, status: 'ended' };
  const connection = await meeting(page, 'history', [ended]);
  let requests = 0;
  const point = { text: 'A recap after retry.', evidence: 'Tomorrow at six.', source_ids: ['retry-source'] };
  await page.route(/\/api\/v1\/rooms\/[^/]+\/recap(?:\?|$)/, async (route) => {
    if (route.request().method() === 'POST') {
      requests++;
      await route.fulfill({ json: { data: { status: 'queued' } } });
    } else await route.fulfill({ json: { data: { configured: true, language: 'hi', status: requests === 0 ? 'not_generated' : requests === 1 ? 'failed' : 'ready',
      error: requests === 1 ? 'The free-tier limit was reached. Please retry later.' : undefined,
      result: requests > 1 ? { summary: point, highlights: [], follow_ups: [], open_questions: [] } : undefined } } });
  });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await page.getByRole('button', { name: 'Generate AI recap' }).click();
  await expect(page.getByRole('alert')).toContainText('free-tier limit');
  await expect(page.getByText(point.text, { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Retry recap' }).click();
  await expect(page.getByText(point.text, { exact: true })).toBeVisible();
  expect(connection.errors).toEqual([]);
});

test('recap quota waits resume automatically and recover from a temporary polling failure', async ({ page }) => {
  const ended = { ...room, is_active: false, status: 'ended' };
  const connection = await meeting(page, 'history', [ended]);
  let posts = 0, polls = 0;
  const point = { text: 'A recap generated automatically after waiting.', evidence: 'Tomorrow at six.', source_ids: ['quota-source'] };
  await page.route(/\/api\/v1\/rooms\/[^/]+\/recap(?:\?|$)/, async (route) => {
    if (route.request().method() === 'POST') {
      posts++;
      await route.fulfill({ json: { data: { status: 'queued' } } });
    } else if (!posts) await route.fulfill({ json: { data: { status: 'not_generated', configured: true, language: 'hi' } } });
    else {
      polls++;
      if (polls === 2) await route.fulfill({ status: 503, json: { error: 'Temporary connection problem.' } });
      else await route.fulfill({ json: { data: polls === 1
        ? { status: 'generating', configured: true, language: 'hi', retry_at: Date.now() / 1000 + 60, error: 'Waiting for provider quota.' }
        : { status: 'ready', configured: true, language: 'hi', result: { summary: point, highlights: [], follow_ups: [], open_questions: [] } } } });
    }
  });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await page.getByRole('button', { name: 'Generate AI recap' }).click();
  await expect(page.getByRole('button', { name: 'Waiting for recap service', exact: true })).toBeDisabled();
  await expect(page.getByRole('status')).toContainText('resume automatically');
  await expect(page.getByRole('alert')).toContainText('Temporary connection problem.');
  await expect(page.getByText(point.text, { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(posts).toBe(1);
  expect(connection.errors).toEqual([]);
});

test('a persisted recap cooldown prevents retry spam and enables retry after the reset', async ({ page }) => {
  await page.clock.install();
  const ended = { ...room, is_active: false, status: 'ended' };
  const connection = await meeting(page, 'history', [ended]);
  let posts = 0;
  const retryAt = Date.now() / 1000 + 60;
  await page.route(/\/api\/v1\/rooms\/[^/]+\/recap(?:\?|$)/, async (route) => {
    if (route.request().method() === 'POST') posts++;
    await route.fulfill({ json: { data: { configured: true, language: 'hi', status: 'failed',
      error: 'The recap quota will reset later.', retry_at: retryAt } } });
  });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await expect(page.getByRole('button', { name: 'Waiting for quota reset', exact: true })).toBeDisabled();
  await expect(page.getByRole('alert')).toContainText('Retry available at');
  expect(posts).toBe(0);
  await page.clock.fastForward(61000);
  await expect(page.getByRole('button', { name: 'Retry recap', exact: true })).toBeEnabled();
  expect(connection.errors).toEqual([]);
});

test('the new home opens an ended conversation as a review rather than a live call', async ({ page }) => {
  const ended = { ...room, is_active: false, status: 'ended' };
  const connection = await meeting(page, 'welcome', [ended]);
  await expect(page.getByRole('button', { name: 'Start a conversation', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Join with a code', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/conversation-home.png', fullPage: true });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Recap', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Generate AI recap' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Enter Conversation', exact: true })).toHaveCount(0);
  expect(connection.errors).toEqual([]);
});

test('an invite to an ended conversation opens its review instead of a dead lobby', async ({ page }) => {
  const ended = { ...room, is_active: false, status: 'ended' };
  const connection = await meeting(page, 'welcome', [ended]);
  await page.route('**/api/v1/rooms/' + room.room_code, (route) => route.fulfill({ json: { data: ended } }));
  await page.goto('/?join=' + room.room_code);
  await expect(page.getByRole('tab', { name: 'Recap', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enter Conversation', exact: true })).toHaveCount(0);
  expect(connection.errors).toEqual([]);
});

for (const failure of ['unavailable', 'html']) {
  test(`saved login survives a ${failure} cold-start response and can be retried`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('kinesia_auth_token', 'saved-token'));
    await page.route('**/api/v1/rooms', (route) => route.fulfill({ json: { ok: true, data: [] } }));
    let ready = false;
    await page.route('**/api/v1/auth/me', async (route) => {
      if (ready) await route.fulfill({ json: { ok: true, data: user } });
      else if (failure === 'html') await route.fulfill({ contentType: 'text/html', body: '<html>Starting...</html>' });
      else await route.fulfill({ status: 503, json: { error: 'Starting backend' } });
    });
    await page.goto('/');
    await expect(page.getByRole('alert')).toContainText('Your sign-in is saved');
    expect(await page.evaluate(() => localStorage.getItem('kinesia_auth_token'))).toBe('saved-token');
    ready = true;
    await page.getByRole('button', { name: 'Retry connection' }).click();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Retry connection' })).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem('kinesia_auth_token'))).toBe('saved-token');
    await expect(page.getByText('Loading Kinesia...')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Sign in/i })).toHaveCount(0);
  });
}
