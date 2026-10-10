'use strict';
const element = id => document.getElementById(id);
const cards = [element('left-card'), element('right-card')];
let battle = null;
let busy = false;
let saved = false;
let uncertainChoice = null;
let retryAction = loadBattle;

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || 'The request failed. Please try again.');
    error.status = response.status;
    throw error;
  }
  return data;
}
function setBusy(value) {
  busy = value;
  element('next-pair').disabled = value;
  element('cards').setAttribute('aria-busy', String(value));
  cards.forEach(card => { card.disabled = value || !battle || saved || uncertainChoice !== null; });
}
function showStatus(message, retry = false) {
  element('battle-status').textContent = message;
  element('retry').hidden = !retry;
}
async function loadBattle(note = '', focusCard = false) {
  setBusy(true);
  element('see-results').hidden = true;
  showStatus(note ? `${note} Loading the next pair…` : saved ? 'Vote saved. Loading the next pair…' : 'Loading a pair…');
  try {
    battle = await request('/api/battles', {});
    saved = false;
    uncertainChoice = null;
    element('next-pair').hidden = true;
    element('model-visibility').textContent = 'Models revealed after you choose';
    ['left', 'right'].forEach(side => {
      element(`${side}-model`).hidden = true;
      element(`${side}-model`).textContent = '';
      element(`${side}-action`).textContent = 'Choose this joke';
    });
    element('cards').hidden = false;
    element('round-info').hidden = false;
    element('word').textContent = battle.word;
    element('left-joke').textContent = battle.jokes[0];
    element('right-joke').textContent = battle.jokes[1];
    cards.forEach(card => { card.classList.remove('selected'); card.classList.remove('revealed'); });
    showStatus(note ? `${note} Choose from the new pair.` : 'Choose your favorite.');
  } catch (error) {
    retryAction = () => loadBattle(note, focusCard);
    element('next-pair').hidden = true;
    if (!battle) {
      element('cards').hidden = true;
      element('round-info').hidden = true;
      element('see-results').hidden = false;
    }
    if (error.status === 409) {
      battle = null;
      element('cards').hidden = true;
      element('round-info').hidden = true;
      showStatus('You’re all caught up. You have voted on every pair in this pool.');
      element('see-results').hidden = false;
    } else {
      showStatus(saved ? `This comparison has a saved vote. ${error.message}` : error.message, true);
    }
  } finally {
    setBusy(false);
    if (focusCard && !saved && battle) cards[0].focus();
  }
}
async function vote(index) {
  if (busy || !battle || saved || (uncertainChoice !== null && uncertainChoice !== index)) return;
  uncertainChoice = index;
  setBusy(true);
  cards[index].classList.add('selected');
  showStatus('Saving your vote…');
  try {
    const result = await request(`/api/battles/${battle.id}/vote`, { choice: index === 0 ? 'left' : 'right' });
    saved = true;
    uncertainChoice = null;
    ['left', 'right'].forEach((side, position) => {
      element(`${side}-model`).textContent = result.models[position];
      element(`${side}-model`).hidden = false;
      element(`${side}-action`).textContent = position === index ? 'Your pick' : 'Other joke';
      cards[position].classList.add('revealed');
    });
    element('model-visibility').textContent = 'Models revealed after your vote';
    showStatus(`Vote saved. Your pick: ${result.models[index]}. Other joke: ${result.models[1 - index]}.`);
    element('next-pair').hidden = false;
  } catch (error) {
    if (error.status === 409) {
      saved = true;
      uncertainChoice = null;
      await loadBattle('This pair already has a saved vote.');
    } else {
      retryAction = () => vote(index);
      showStatus(`Vote could not be confirmed. ${error.message} Retry the same choice.`, true);
    }
  } finally {
    setBusy(false);
    if (saved && !element('next-pair').hidden && !element('battle-view').hidden) element('next-pair').focus({ preventScroll: true });
  }
}
async function loadLeaderboard() {
  element('refresh-results').disabled = true;
  element('leaderboard-status').textContent = 'Loading results…';
  try {
    const results = await request('/api/leaderboard');
    element('vote-count').textContent = results.total_votes;
    element('pair-count').textContent = results.pool_pairs;
    const words = results.word_bucket || [];
    element('word-count').textContent = words.length;
    element('word-list').textContent = words.join(', ');
    element('unavailable-models').textContent = (results.unavailable_models || []).length
      ? `Not available on OpenRouter: ${results.unavailable_models.join(', ')}.` : '';
    element('model-notes').textContent = (results.model_notes || []).join(' ');
    element('rankings').replaceChildren();
    let rank = 0;
    let previousRate = null;
    results.models.forEach((model, index) => {
      if (model.win_rate !== null && model.win_rate !== previousRate) rank = index + 1;
      previousRate = model.win_rate;
      const row = document.createElement('tr');
      const values = [model.win_rate === null ? '—' : rank, model.name, model.reasoning_label || 'Default',
        model.win_rate === null ? 'No votes' : `${(model.win_rate * 100).toFixed(1)}%`,
        model.wins, model.losses, model.comparisons, model.unique_jokes];
      values.forEach(value => {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      });
      element('rankings').append(row);
    });
    element('leaderboard-status').textContent = results.total_votes ? '' : 'No votes yet. Choose a joke in Battle to start the leaderboard.';
    element('sample-note').textContent = `The current pool contains ${results.pool_pairs} pairs. Each model faces a limited set of words and opponents. This sample describes preferences for these jokes; it does not establish which model is funniest overall.`;
  } catch (error) {
    element('leaderboard-status').textContent = `Could not load results. ${error.message}`;
  } finally { element('refresh-results').disabled = false; }
}
function showView(name) {
  ['battle', 'leaderboard'].forEach(view => {
    element(`${view}-view`).hidden = view !== name;
    element(`${view}-tab`).classList.toggle('active', view === name);
    if (view === name) element(`${view}-tab`).setAttribute('aria-current', 'page');
    else element(`${view}-tab`).removeAttribute('aria-current');
  });
  if (name === 'leaderboard') loadLeaderboard();
}
cards.forEach((card, index) => card.addEventListener('click', () => vote(index)));
element('retry').addEventListener('click', () => retryAction());
element('next-pair').addEventListener('click', () => { if (!busy && saved) return loadBattle('', true); });
element('see-results').addEventListener('click', () => showView('leaderboard'));
element('battle-tab').addEventListener('click', () => showView('battle'));
element('leaderboard-tab').addEventListener('click', () => showView('leaderboard'));
element('refresh-results').addEventListener('click', loadLeaderboard);
loadBattle();
