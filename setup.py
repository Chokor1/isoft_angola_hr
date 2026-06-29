from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in isoft_angola_hr/__init__.py
from isoft_angola_hr import __version__ as version

setup(
	name="isoft_angola_hr",
	version=version,
	description="Angola HR and Payroll",
	author="Abbass Chokor",
	author_email="abbasschokor225@gmail.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
