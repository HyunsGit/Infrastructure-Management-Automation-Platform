// summary_menu.js
const minPieAnglePlugin = {
    id: 'minPieAngle',
    beforeDatasetDraw(chart, args, options) {
        const dataset = chart.data.datasets[0];
        const data = dataset.data;
        const total = data.reduce((a, b) => a + b, 0);
        if (total === 0) return;
        const n = data.length;
        const minAngle = (options && options.minAngle ? options.minAngle : 4);
        const minFrac = minAngle / 360;
        let adjustedData = data.slice();
        let deficit = 0;
        for (let i = 0; i < n; ++i) {
            let frac = data[i] / total;
            if (frac < minFrac && data[i] !== 0) {
                deficit += minFrac * total - data[i];
                adjustedData[i] = minFrac * total;
            }
        }
        if (deficit > 0) {
            let surplusIndices = [];
            let surplusSum = 0;
            for (let i = 0; i < n; ++i) {
                let frac = data[i] / total;
                if (frac >= minFrac) {
                    surplusIndices.push(i);
                    surplusSum += data[i];
                }
            }
            for (let i of surplusIndices) {
                let reduce = deficit * (data[i] / surplusSum);
                adjustedData[i] -= reduce;
                if (adjustedData[i] < 0) adjustedData[i] = 0;
            }
        }
        dataset._originalData = dataset._originalData || data.slice();
        dataset.data = adjustedData;
    },
    afterDatasetDraw(chart, args) {
        const dataset = chart.data.datasets[0];
        if (dataset._originalData) {
            dataset.data = dataset._originalData;
            delete dataset._originalData;
        }
    }
};
Chart.register(minPieAnglePlugin);

const barColorsPerProject = [
    '#2196F3', '#F44336', '#8e44ad', '#43A047', '#FFB300',
    '#009688', '#FFC107', '#795548', '#5C6BC0', '#00BCD4'
];

const flavorColors = [
    '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF',
    '#C9CBCF', '#B8D9B9', '#F39389', '#FFD166', '#06D6A0',
    '#D81B60', '#1E88E5', '#FBC02D', '#00ACC1', '#7B1FA2',
    '#90A4AE', '#A1887F', '#F57C00', '#388E3C', '#1976D2'
];

function sortProjects(labels, values, method) {
    const combined = labels.map((lbl, idx) => ({ label: lbl, value: values[idx] }));
    if (method === "quantity") {
        combined.sort((a, b) => b.value - a.value);
    } else {
        combined.sort((a, b) => a.label.localeCompare(b.label));
    }
    return [combined.map(obj => obj.label), combined.map(obj => obj.value)];
}

let barChart;
function drawBarChart(sortedLabels, sortedValues, title) {
    const ctx = document.getElementById('projectBarChart').getContext('2d');
    if (barChart) {
        barChart.destroy();
    }
    barChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: sortedLabels,
            datasets: [{
                label: 'VM Quantity',
                data: sortedValues,
                borderWidth: 1,
                borderSkipped: false,
                borderRadius: 10,
                backgroundColor: barColorsPerProject.slice(0, sortedLabels.length)
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            layout: { padding: { right: 60 } },
            animation: { duration: 1200, easing: 'easeOutQuart' },
            plugins: {
                legend: { display: false, labels: { color: '#fff' } },
                title: { display: true, text: title, color: '#2196F3', font: { size: 20, weight: 'bold' } },
                datalabels: {
                    color: '#fff',
                    font: { weight: 'bold', size: 18 },
                    clamp: true,
                    clip: false,
                    padding: { right: 8, left: 8 },
                    formatter: function (value) { return value; },
                    anchor: 'end',
                    align: function (context) {
                        const value = context.dataset.data[context.dataIndex];
                        return value >= 10 ? 'end' : 'right';
                    }
                }
            },
            scales: {
                x: {
                    title: { display: true, text: 'VM Quantity', color: '#fff' },
                    beginAtZero: true,
                    ticks: { color: '#fff' }
                },
                y: {
                    title: { display: true, text: 'Project', color: '#fff' },
                    ticks: { color: '#fff' }
                }
            }
        },
        plugins: [ChartDataLabels]
    });
}

let [sortedProjLabels, sortedProjValues] = sortProjects(chartData.project_labels, chartData.project_values, "quantity");
drawBarChart(sortedProjLabels, sortedProjValues, 'VM Quantity per Project');

document.getElementById('sortSelector').addEventListener('change', function () {
    const selected = this.value;
    const [newLabels, newValues] = sortProjects(chartData.project_labels, chartData.project_values, selected);
    drawBarChart(newLabels, newValues, 'VM Quantity per Project');
});

function makePieChart(id, labels, values, title, colors) {
    const ctx = document.getElementById(id).getContext('2d');
    return new Chart(ctx, {
        type: 'pie',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors || [
                    '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF',
                    '#C9CBCF', '#B8D9B9', '#F39389', '#FFD166', '#06D6A0'
                ],
                borderColor: '#232946',
                borderWidth: 5,
                hoverOffset: 16,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '45%',
            animation: { duration: 1200, easing: 'easeOutQuart' },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { font: { size: 18, weight: 'bold' }, color: '#fff' }
                },
                title: { display: true, text: title, color: '#2196F3', font: { size: 20, weight: 'bold' } },
                datalabels: {
                    color: '#fff',
                    font: { weight: 'bold', size: 18 },
                    formatter: function (value) { return value; }
                },
                minPieAngle: { minAngle: 20 }
            }
        },
        plugins: [ChartDataLabels, minPieAnglePlugin]
    });
}
makePieChart('statusPieChart', chartData.status_labels, chartData.status_values, 'VM Status');
makePieChart('azPieChart', chartData.az_labels, chartData.az_values, 'Availability Zones');

let [sortedFlavorLabels, sortedFlavorValues] = sortProjects(chartData.flavor_labels, chartData.flavor_values, "quantity");

let flavorBarChart;
function drawFlavorBarChart(labels, values, colors, title) {
    const ctx = document.getElementById('flavorPieChart').getContext('2d');
    if (flavorBarChart) flavorBarChart.destroy();
    flavorBarChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Flavor Quantity',
                data: values,
                backgroundColor: colors.slice(0, labels.length),
                borderWidth: 1,
                borderRadius: 10,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            layout: { padding: { right: 50 } },
            animation: { duration: 1200, easing: 'easeOutQuart' },
            plugins: {
                legend: { display: false },
                title: { display: true, text: title, color: '#2196F3', font: { size: 20, weight: 'bold' } },
                datalabels: {
                    color: '#fff',
                    font: { weight: 'bold', size: 18 },
                    clamp: true,
                    clip: false,
                    padding: { right: 6, left: 6 },
                    anchor: 'end',
                    formatter: function (value) { return value; },
                    align: function (context) {
                        const value = context.dataset.data[context.dataIndex];
                        return value >= 10 ? 'end' : 'right';
                    }
                }
            },
            scales: {
                x: {
                    title: { display: true, text: 'Quantity', color: '#fff' },
                    ticks: { color: '#fff' },
                    beginAtZero: true,
                },
                y: {
                    title: { display: true, text: 'Flavor', color: '#fff' },
                    ticks: { color: '#fff' },
                }
            }
        },
        plugins: [ChartDataLabels]
    });
}
drawFlavorBarChart(sortedFlavorLabels, sortedFlavorValues, flavorColors, 'Top 10 Flavors');

document.getElementById('flavorSortSelector').addEventListener('change', function () {
    const selected = this.value;
    const [newLabels, newValues] = sortProjects(chartData.flavor_labels, chartData.flavor_values, selected);
    drawFlavorBarChart(newLabels, newValues, flavorColors, 'Top 10 Flavors');
});
