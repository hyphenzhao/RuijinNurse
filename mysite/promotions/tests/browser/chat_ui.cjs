// Run through chat_ui.py: all data, HTTP services and browser state are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {base, session, model, patients, production} = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
    const browser = await chromium.launch({
        ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath:process.env.CHROMIUM_EXECUTABLE } : {}),
    });
    try {
        const ctx = await browser.newContext({ serviceWorkers:'block' });
        await ctx.addCookies([{ name:'sessionid', value:session, url:base }]);
        await ctx.addInitScript(() => {
            window.__cspViolations = [];
            document.addEventListener('securitypolicyviolation', event => {
                window.__cspViolations.push(event.violatedDirective);
            });
        });
        const page = await ctx.newPage();
        const errors = [];
        const reports = [];
        let blockRenderers = false;
        let stallStop = false;
        let stopRequests = 0;
        let streamRequests = 0;
        page.on('pageerror', error => errors.push(error.message));
        page.on('response', response => {
            if (response.url().endsWith('/timing/report/')) reports.push(response.status());
        });
        await ctx.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== base) return route.abort(); // no internet access in any case
            if (/\/(unity|media)\//i.test(url.pathname) || /\.(mp4|wasm|unityweb|data)$/.test(url.pathname)) return route.abort();
            if (blockRenderers && /\/(markdown-it|purify)\.min\.js$/.test(url.pathname)) return route.abort();
            if (url.pathname.endsWith('/promotion/stop/')) {
                ++stopRequests;
                if (stallStop) await new Promise(resolve => setTimeout(resolve, 3500));
            }
            if (url.pathname.endsWith('/promotion/stream/')) ++streamRequests;
            return route.continue();
        });
        async function reset({thinking = true, missingRenderers = false} = {}) {
            blockRenderers = missingRenderers;
            reports.length = 0;
            errors.length = 0;
            const initial = await page.goto(`${base}/promotion/`, {waitUntil:'load'});
            assert.equal(initial.status(), 200);
            if (production) assert.equal(initial.headers()['x-content-type-options'], 'nosniff');
            assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
            await page.locator('#modeSingleModel').check();
            await page.locator('#singleModelSelect').selectOption('model:fixture-ollama');
            await page.locator('#showThinkingToggle').setChecked(thinking);
            // Keep test assertions independent of restored history.
            await page.evaluate(() => document.getElementById('chat-messages').replaceChildren());
        }
        async function submit(question) {
            await page.locator('#promotionText').fill(question);
            await page.locator('#submit-button').click();
        }
        async function waitFinished() {
            await page.waitForFunction(() => !document.getElementById('submit-button').disabled, null, {timeout:8000});
        }
        async function waitReport() {
            for (let n=0; n<50 && !reports.length; n++) await page.waitForTimeout(20);
            assert.deepEqual(reports, [200]);
        }
        const message = page.locator('#chat-messages');

        if (production) {
            for (const [path, mime] of [
                ['/static/promotions/vendor/markdown-it-13.0.1/markdown-it.min.js', /javascript/],
                ['/static/promotions/js/purify.min.js', /javascript/],
                ['/static/promotions/css/bootstrap.min.css', /text\/css/],
            ]) {
                const response = await fetch(base + path);
                assert.equal(response.status, 200);
                assert(mime.test(response.headers.get('content-type')));
                assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
                await response.arrayBuffer();
            }
            const unity = await fetch(base + '/static/promotions/unity/fixture.js');
            assert.equal(unity.status, 200);
            assert.equal(unity.headers.get('cross-origin-opener-policy'), 'same-origin');
            assert.equal(unity.headers.get('cross-origin-embedder-policy'), 'require-corp');
            await unity.arrayBuffer();
            const audio = await fetch(base + '/static/promotions/fixture.wav');
            assert.equal(audio.status, 200);
            assert.equal(audio.headers.get('cache-control'), 'no-store');
            await audio.arrayBuffer();
            for (const path of ['/static/promotions/input.txt', '/static/promotions/input.txt.']) {
                const blocked = await fetch(base + path);
                assert.equal(blocked.status, 404);
                await blocked.arrayBuffer();
            }
            // Node fetch deliberately ignores custom Host headers; use raw HTTP.
            const invalidHostStatus = await new Promise((resolve, reject) => {
                require('node:http').get(base + '/static/promotions/js/purify.min.js', {
                    headers: {Host: 'unexpected.invalid'}, agent: false,
                }, response => {
                    response.resume();
                    response.on('end', () => resolve(response.statusCode));
                }).on('error', reject);
            });
            assert.equal(invalidHostStatus, 400);
            console.log('PASS real Waitress + DEBUG=False: static assets, MIME/security headers, Unity isolation, private paths and Host validation');
        }

        await reset();
        await submit('audit-first-token');
        await message.getByText('首字已到达', {exact:true}).waitFor();
        assert(await page.locator('#submit-button').isDisabled());
        assert(!(await message.innerText()).includes('稍后完成'));
        await waitFinished();
        assert((await message.innerText()).includes('首字已到达，稍后完成'));
        await waitReport();
        assert.deepEqual(errors, []);
        assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
        console.log('PASS first answer is visible before model completion; SSE is not buffered');

        await reset();
        await submit('audit-partial');
        await waitFinished();
        assert((await message.innerText()).includes('合成答案被截断于此'));
        assert((await message.innerText()).includes('回答中断'));
        assert.equal(await message.locator('.answer[data-result="interrupted"]').count(), 1);
        assert.equal(reports.length, 0);
        await submit('audit-next');
        await waitFinished();
        assert((await message.innerText()).includes('历史未保留截断答案'));
        await waitReport();
        console.log('PASS interrupted answer clearly labelled and excluded from subsequent model history');

        await reset();
        await submit('audit-empty');
        await waitFinished();
        assert((await message.innerText()).includes('回答失败'));
        assert.equal(await message.locator('.answer').count(), 1);
        assert.equal(reports.length, 0);
        console.log('PASS empty response visibly failed');

        for (const thinking of [false, true]) {
            await reset({thinking});
            await submit(`audit-normal-${thinking}`);
            await waitFinished();
            assert.equal(await message.locator('.answer').count(), 1);
            assert.equal(await message.locator('.answer[data-result="success"]').count(), 1);
            assert((await message.innerText()).includes('合成问答正常结束'));
            await waitReport();
            assert.deepEqual(errors, []);
        }
        console.log('PASS one answer bubble with thinking enabled/disabled; no internet needed; timing reported');

        await reset({missingRenderers:true});
        await submit('audit-no-renderers');
        await waitFinished();
        assert((await message.innerText()).includes('合成问答正常结束'));
        assert.equal(await message.locator('.answer[data-result="success"]').count(), 1);
        assert.deepEqual(errors, []);
        await waitReport();
        assert.equal(await page.evaluate(() => document.querySelector('#chat-messages script')), null);
        console.log('PASS safe plain-text fallback and timing report when both render libraries fail');

        await reset();
        await submit('audit-cancel');
        await message.getByText('合成思考开始').waitFor();
        const stopping = page.waitForResponse(r => r.url().endsWith('/promotion/stop/') && !!r.request().postData());
        await page.locator('#stop-button').click();
        assert.equal((await stopping).status(),200);
        await waitFinished();
        assert((await message.innerText()).includes('已停止生成'));
        let cancelState = {};
        for (let n=0;n<35;n++) {
            cancelState = await (await fetch(`${model}/test/cancel-state`)).json();
            if ('open_after_stop' in cancelState) break;
            await new Promise(resolve=>setTimeout(resolve,100));
        }
        assert.equal(cancelState.open_after_stop, false);
        console.log('PASS stop cancels upstream socket while model is idle');

        await reset();
        stallStop = true;
        const oldStopRequests = stopRequests;
        const oldStreamRequests = streamRequests;
        await submit('audit-first-no-stop');
        await waitFinished();
        assert.equal(stopRequests, oldStopRequests);
        assert.equal(streamRequests, oldStreamRequests + 1);
        console.log('PASS first/new submission does not call stop endpoint');

        await submit('audit-cancel-stalled');
        await page.waitForFunction(() => document.querySelectorAll('.thinking-content').length > 1);
        await page.locator('#stop-button').click();
        const stopStart = Date.now();
        await waitFinished();
        assert(Date.now() - stopStart < 2500);
        assert((await message.innerText()).includes('已停止生成'));
        stallStop = false;
        console.log('PASS unavailable stop endpoint does not keep browser blocked');

        await reset();
        async function selectPatient(id) {
            await page.locator('#patientFloatBtn').click();
            await page.locator(`#patientList [data-patient-id="${id}"]`).click();
            await page.waitForFunction(expected => document.getElementById('patientIdInput').value === String(expected) && !document.getElementById('submit-button').disabled, id);
        }
        await selectPatient(patients[0]);
        await submit('audit-patient-A');
        await waitFinished();
        await selectPatient(patients[1]);
        assert(!(await message.innerText()).includes('audit-patient-A'));
        await submit('audit-patient-B');
        await waitFinished();
        await selectPatient(patients[0]);
        const restored = await message.innerText();
        assert(restored.includes('audit-patient-A'));
        assert(!restored.includes('audit-patient-B'));
        assert.deepEqual(errors, []);
        console.log('PASS switching patients clears previous display and restores isolated patient history');

        await submit('audit-cancel-switch-patient');
        await message.getByText('合成思考开始').waitFor();
        await selectPatient(patients[1]);
        await page.waitForTimeout(1700);
        assert(!(await message.innerText()).includes('audit-cancel-switch-patient'));
        assert(!(await message.innerText()).includes('停止之后不应显示的输出'));
        assert((await message.innerText()).includes('audit-patient-B'));
        console.log('PASS patient switch waits for active answer cancellation before replacing the conversation');

        await page.reload({waitUntil:'load'});
        assert((await message.innerText()).includes('audit-patient-B'));
        assert(!(await message.innerText()).includes('audit-patient-A'));
        const media = await page.evaluate(() => ({
            unsafe: findEmbeddedMedia('[bad](javascript:fixture.pdf)'),
            valid: findEmbeddedMedia('[资料](/static/fixture.pdf)'),
        }));
        assert.equal(media.unsafe.length, 0);
        assert.equal(media.valid.length, 1);
        assert.deepEqual(errors, []);
        console.log('PASS reload restores selected patient history and embedded media rejects unsafe URL schemes');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
