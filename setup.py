from setuptools import setup

setup(name='aciClient',
      version='1.00',
      description='aci communication helper class',
      url='http://www.netcloud.ch',
      author='mze',
      author_email='nc_dev@netcloud.ch',
      license='MIT',
      packages=['aciClient'],
      install_requires=['requests>=2.25.0 , <3', 'pyOpenSSL>=19.1.0, <20'],
      zip_safe=False)
