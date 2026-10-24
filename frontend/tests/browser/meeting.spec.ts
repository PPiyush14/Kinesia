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
  await expect(page.getByRole('button', { name: 'End meeting', exact: true })).toBeVisible();
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
  await page.screenshot({ path: 'test-results/meeting-mobile.png', fullPage: true });
  expect(connection.errors).toEqual([]);
});

test('creator reopening a room from history keeps the End meeting control', async ({ page }) => {
  const connection = await meeting(page, 'history');
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await page.getByRole('button', { name: 'Re-join', exact: true }).click();
  await page.getByRole('button', { name: 'Enter Conversation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End meeting', exact: true })).toBeVisible();
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
  const second = { ...room, id: 'second', room_code: 'kin-456-def', title: 'Second conversation' };
  const connection = await meeting(page, 'history', [room, second]);
  let release!: () => void;
  let firstRequested = false;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/v1/rooms/*/insights', async (route) => {
    if (route.request().url().includes(room.room_code)) {
      firstRequested = true;
      await gate;
      await route.fulfill({ json: { data: { executive_summary: 'Stale first conversation notes' } } });
    } else await route.fulfill({ json: { data: { executive_summary: 'Correct second conversation notes' } } });
  });
  await page.getByRole('heading', { name: room.title, exact: true }).click();
  await expect.poll(() => firstRequested).toBe(true);
  await page.getByRole('heading', { name: second.title, exact: true }).click();
  await expect(page.getByText('Correct second conversation notes', { exact: true })).toBeVisible();
  const response = page.waitForResponse((result) => result.url().includes(`${room.room_code}/insights`));
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByText('Correct second conversation notes', { exact: true })).toBeVisible();
  await expect(page.getByText('Stale first conversation notes', { exact: true })).toHaveCount(0);
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
