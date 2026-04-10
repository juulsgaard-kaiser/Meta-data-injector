/** Progress Ring — SVG circular progress indicator */
const ProgressRing = {
    create(percent = 0, size = 60, strokeWidth = 4, color = 'var(--accent-blue)') {
        const radius = (size - strokeWidth) / 2;
        const circumference = 2 * Math.PI * radius;
        const offset = circumference - (percent / 100) * circumference;
        return `
            <svg class="progress-ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
                <circle cx="${size/2}" cy="${size/2}" r="${radius}" fill="none" stroke="var(--border-subtle)" stroke-width="${strokeWidth}"/>
                <circle class="progress-ring-circle" cx="${size/2}" cy="${size/2}" r="${radius}" fill="none"
                    stroke="${color}" stroke-width="${strokeWidth}" stroke-linecap="round"
                    stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"
                    transform="rotate(-90 ${size/2} ${size/2})"
                    style="transition: stroke-dashoffset 0.5s ease-out"/>
                <text x="${size/2}" y="${size/2}" text-anchor="middle" dy="0.35em"
                    fill="var(--text-primary)" font-size="${size * 0.22}px" font-weight="600"
                    font-family="var(--font-mono)">${Math.round(percent)}%</text>
            </svg>`;
    },

    update(el, percent) {
        const circle = el.querySelector('.progress-ring-circle');
        if (!circle) return;
        const r = parseFloat(circle.getAttribute('r'));
        const c = 2 * Math.PI * r;
        circle.style.strokeDashoffset = c - (percent / 100) * c;
        const text = el.querySelector('text');
        if (text) text.textContent = `${Math.round(percent)}%`;
    }
};
