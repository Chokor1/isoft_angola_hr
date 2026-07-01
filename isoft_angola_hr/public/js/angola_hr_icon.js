// Angola HR Icon - Navbar shortcut to the Angola HR Dashboard.
// Shown only to HR Manager (the dashboard page role).
(function () {
	'use strict';

	const PAGE = '/app/angola-hr-dashboard';
	const ROLES = ['HR Manager'];

	function canAccess() {
		try {
			const myRoles = frappe.user_roles || [];
			return ROLES.some((r) => myRoles.includes(r));
		} catch (e) {
			return false;
		}
	}

	function initAngolaHrIcon() {
		// Don't add it twice (route changes can re-run includes)
		if (document.getElementById('angola-hr-navbar')) return;
		if (!canAccess()) return;

		// Inline onclick is required: Frappe's router hijacks /app/* <a> clicks unless
		// the link already has an onclick. We open in a new tab and return false.
		const icon = `
			<li class='nav-item dropdown dropdown-notifications dropdown-mobile angola-hr-icon' title="Angola HR" aria-label="Angola HR">
				<a href="${PAGE}" class="angola-hr-button" id="angola-hr-navbar" target="_blank" rel="noopener"
					onclick="window.open('${PAGE}', '_blank'); return false;">
					<i class="fa fa-users"></i>
				</a>
			</li>`;

		const $navbarList = $('header.navbar > .container > .navbar-collapse > ul');
		if ($navbarList.length) {
			$navbarList.prepend(icon);
		}

		if (!document.getElementById('angola-hr-icon-styles')) {
			$('head').append(`
				<style id="angola-hr-icon-styles">
					.angola-hr-icon { margin-right: 8px; }
					.angola-hr-button {
						display: flex; align-items: center; justify-content: center;
						width: 40px; height: 40px;
						background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
						color: #fff; text-decoration: none; border-radius: 50%;
						transition: all 0.3s ease; box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
						position: relative; overflow: hidden; cursor: pointer;
					}
					.angola-hr-button:hover {
						background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%);
						color: #fff; text-decoration: none;
						transform: translateY(-2px) scale(1.05);
						box-shadow: 0 4px 16px rgba(37, 99, 235, 0.5);
					}
					.angola-hr-button:active {
						transform: translateY(0) scale(0.98);
						box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
					}
					.angola-hr-button i { color: #fff; font-size: 18px; text-shadow: 0 1px 2px rgba(0,0,0,0.2); }
					.angola-hr-button::before {
						content: ''; position: absolute; top: 0; left: -100%;
						width: 100%; height: 100%;
						background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
						transition: left 0.5s;
					}
					.angola-hr-button:hover::before { left: 100%; }
					@media (max-width: 768px) {
						.angola-hr-button { width: 36px; height: 36px; }
						.angola-hr-button i { font-size: 16px; }
					}
				</style>
			`);
		}
	}

	if (typeof frappe !== 'undefined' && frappe.user) {
		$(document).ready(initAngolaHrIcon);
	} else {
		$(document).on('frappe:ready', initAngolaHrIcon);
	}
})();
