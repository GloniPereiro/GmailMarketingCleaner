let scanInProgress = false;
let currentSort = { key: 'count', dir: 'desc' };
let currentData = [];

const scanBtn = document.getElementById('scan-btn');
const exportBtn = document.getElementById('export-btn');
const progressContainer = document.getElementById('progress-container');
const progressBar = document.getElementById('progress-bar');
const scanStatus = document.getElementById('scan-status');
const domainFilter = document.getElementById('domain-filter');
const resultsBody = document.getElementById('results-body');

const deleteProgressContainer = document.getElementById("delete-progress-container");
const deleteProgressBar = document.getElementById("delete-progress-bar");

scanBtn.onclick = () => {
    if (scanInProgress) return;
    scanInProgress = true;

    scanStatus.textContent = "Scanning...";
    progressContainer.style.display = "block";
    progressBar.style.width = "0%";

    const days = document.getElementById('days').value;
    const label = document.getElementById('label').value;

    fetch('/start-scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days, label })
    }).then(() => pollProgress());
};

exportBtn.onclick = () => {
    window.location = '/export';
};

function pollProgress() {
    fetch('/progress')
        .then(r => r.json())
        .then(data => {
            const percent = data.total > 0 ? Math.round((data.done / data.total) * 100) : 0;
            progressBar.style.width = percent + "%";

            if (!data.in_progress) {
                scanInProgress = false;
                scanStatus.textContent = "Completed.";
                exportBtn.disabled = false;
                loadResults();
            } else {
                scanStatus.textContent = `Scanning... ${percent}%`;
                setTimeout(pollProgress, 300);
            }
        });
}

function loadResults() {
    fetch('/results')
        .then(r => r.json())
        .then(data => {
            currentData = data.senders;
            renderTable();
        });
}

function renderTable() {
    resultsBody.innerHTML = "";

    let rows = [...currentData];

    rows.sort((a, b) => {
        const key = currentSort.key;
        let va = a.info[key];
        let vb = b.info[key];

        if (key === 'count') {
            va = Number(va);
            vb = Number(vb);
        } else {
            va = (va || '').toLowerCase();
            vb = (vb || '').toLowerCase();
        }

        if (va < vb) return currentSort.dir === 'asc' ? -1 : 1;
        if (va > vb) return currentSort.dir === 'asc' ? 1 : -1;
        return 0;
    });

    const filter = domainFilter.value.toLowerCase();

    rows.forEach(item => {
        if (filter && !item.info.domain.toLowerCase().includes(filter)) return;

        const tr = document.createElement('tr');

        tr.innerHTML = `
            <td>${item.sender}</td>
            <td>${item.info.email}</td>
            <td>${item.info.domain}</td>
            <td>${item.info.count}</td>
            <td><button class="btn btn-danger" data-sender='${item.sender}'>Delete</button></td>
        `;

        resultsBody.appendChild(tr);
    });

    attachDeleteHandlers();
}

function attachDeleteHandlers() {
    document.querySelectorAll('button[data-sender]').forEach(btn => {
        btn.onclick = () => {
            const sender = btn.getAttribute('data-sender');
            if (!confirm(`Delete all emails from: ${sender}?`)) return;

            deleteProgressContainer.style.display = "block";
            deleteProgressBar.style.width = "0%";
            pollDeleteProgress();

            fetch('/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sender })
            })
            .then(r => r.json())
            .then(() => {});
        };
    });
}

function pollDeleteProgress() {
    fetch('/delete-progress')
        .then(r => r.json())
        .then(data => {
            if (!data.in_progress) {
                deleteProgressContainer.style.display = "none";
                loadResults();
                return;
            }

            const percent = data.total > 0
                ? Math.round((data.done / data.total) * 100)
                : 0;

            deleteProgressBar.style.width = percent + "%";

            setTimeout(pollDeleteProgress, 300);
        });
}

domainFilter.oninput = () => renderTable();

document.querySelectorAll('#results-table th[data-sort]').forEach(th => {
    th.onclick = () => {
        const key = th.getAttribute('data-sort');
        if (currentSort.key === key) {
            currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
        } else {
            currentSort.key = key;
            currentSort.dir = key === 'count' ? 'desc' : 'asc';
        }
        renderTable();
    };
});

loadResults();
