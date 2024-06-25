# Changelog

## [2.18.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.17.1...v2.18.0) (2024-06-25)


### Features

* add time delay to try to prevent socket error ([cbb6063](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/cbb60639dd53d29cc7bc0b64946dcc4c6315778c))
* add timeout to create fastq json file ([39ddd91](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/39ddd91a2e307817ce29e8ef8627e6aee0808ce7))


### Bug Fixes

* decrease number of fastq files archived at the same time ([647be57](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/647be57801bd080172785087ee30b0ffeb61dc7f))
* remove duplicate host key in run_analysis ([f51ff7e](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/f51ff7eb4dd2e7975390d3951f6a695f387c8a3a))
* remove duplicate name key in copy workflow ([d506e2b](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/d506e2bc1c169043c70976879a7b0a6b82d2912a))
* rename task to not overlap with workflow name ([7e5bd45](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/7e5bd45aa76fe23f5279c36edcc3d9ce5bde0764))


### Documentation

* update version ([ca31abe](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/ca31abe1efc656f5ecd274a01b8b760a163a2d7f))

### [2.17.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.17.0...v2.17.1) (2024-06-19)


### Bug Fixes

* update version information ([a1f590a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/a1f590a46edb86ab8fc4e807ecf8fa30f96539b3))

## [2.17.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.16.2...v2.17.0) (2024-06-19)


### Features

* decrease number of parallele archive events ([87a5e1f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/87a5e1f19139569ccf8bb11691de6bce8d371002))

### [2.16.2](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.16.1...v2.16.2) (2024-06-18)


### Bug Fixes

* add retry to archiving of files ([cc4e41a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/cc4e41afd9da0dc1a16c9d1f95264d05355ef29b))
* remove output part from archive file ([5b4b317](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/5b4b317a1bf60626ac03a059be72fc6725ec8dce))
* update archive_sequence_run to fail correctly ([f5dc2d2](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/f5dc2d2f724bb63414a18c7cd4af4bd2cd2e1f8f))


### Documentation

* update version ([f987f41](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/f987f41b32a2de0f79e016bbd9e3b0474626f0a3))

### [2.16.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.16.0...v2.16.1) (2024-06-18)


### Bug Fixes

* add error handling and retries ([bdd0fa4](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/bdd0fa442578d3b7bcaeccd2164535ec65735e03))
* change run to do ([4adce00](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4adce00fce72c26da93cf24373f639adb558a879))
* update failure handling ([efd4ea3](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/efd4ea306274333b29bd118859e0945cbece50ff))


### Documentation

* update version ([d2ddc2a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/d2ddc2acf5b51d7f18975d77d5eb2416ad2f39a7))

## [2.16.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.15.1...v2.16.0) (2024-06-16)


### Features

* correct subject text ([15c2cb9](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/15c2cb9c83f47babc56319e806b1aaffab3e1b32))


### Documentation

* update pack yaml ([a12e9bd](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/a12e9bd19010b7dc52fab3b02082c813690d9f12))

### [2.15.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.15.0...v2.15.1) (2024-06-16)


### Documentation

* update ([267e3d4](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/267e3d47d48abb2a33b08c1d76f70dd2f0a5b61f))
* update version ([0e7979f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/0e7979f9275229a53b17f8edfeed0b1944e48a6c))

## [2.15.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.14.3...v2.15.0) (2024-06-14)


### Features

* Get sample sheet content in e-mail ([7ccde8a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/7ccde8a006bfd08a478adec6f88f84343307803d))
* increase timeouts in run_analysis ([dc5da39](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/dc5da394f91b1e046d2f67331b06b3e700e0a981))


### Bug Fixes

* add mail information for lab ([4fee89f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4fee89fba214ada1563e6216226180b82c93d760))
* Call to get_samplesheet_content ([bd42f81](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/bd42f81fd63b5b83d622bb05480ac0699dd57007))
* Correct variable name ([1341cb3](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/1341cb3a0d582f81b0f73585775088285fe85599))
* Handle succeeded/failed task ([41ff82b](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/41ff82b5868fd7aaee822bbda0a68f22227f8307))
* update incorrect fetching of mail settings ([1ed24a5](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/1ed24a559adc59dfd5ac3c5e95c5e24630c32366))

### [2.14.3](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.14.2...v2.14.3) (2024-06-08)


### Bug Fixes

* add missing %> to error notification ([4977836](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4977836f1840743c914299867868a94c4df01881))


### Documentation

* update version ([8c8fe40](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/8c8fe406582bf9c6c0ad4ee2e7fe8af3307f9fe1))

### [2.14.2](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.14.1...v2.14.2) (2024-06-08)


### Bug Fixes

* set correct variable name: wp to workpackage ([9fbfe3f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/9fbfe3f5b61bdb4baf124a1f14712426fba53d14))


### Documentation

* update version ([aa299db](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/aa299db9a6cfc3e11a4c4d1611b047188511b1e4))

### [2.14.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.14.0...v2.14.1) (2024-06-05)


### Bug Fixes

* incorrect mail ([28f5037](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/28f50374b94c91bf9718cb9104799da3e7eb3fae))
* update error message handling ([8b7c457](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/8b7c457380580c75d508d43668bddcfca3b97ca7))


### Documentation

* change version ([2d1fe03](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/2d1fe03568058ed3685293173c5e7f3dd92ed792))

## [2.14.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.13.1...v2.14.0) (2024-05-08)


### Features

* add a delay to detection of fastq-files ([0e8e76f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/0e8e76f5f2dec3dfa28f1b8d38fee060288b0808))
* add access key to curl commands ([6463cd9](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/6463cd9901c52bebe7ba9b76c9d01419a8a92bdf))
* add analysis file sensor and rules/action for it ([1478e2f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/1478e2fac9ae62b78ead0770af4665ddc366ba30))
* add api-key to archive workflow ([e50abc4](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/e50abc4c6fa66befed09072b2936f0d03f029663))
* add demultiplex local and wait for machine task ([8602dee](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/8602deedac58ccc3168d3fb57e802045c6db58b6))
* add interop archive workflow ([5cc8c7b](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/5cc8c7b6db871d94ea8e49f6acb7e532f32b6380))
* add option to add cold storage ([aa71dac](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/aa71dacdf0d913c993667a2b432dcf0f32ac5f4e))
* add sensor used to query processing api ([80375e3](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/80375e36e56f9c685c2845083dccaf153b9e2f07))
* add status updates ([774dedf](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/774dedf976d5f2fe1938a4423a12b13ddf367091))
* add tasks to update fastq file information ([ea9e999](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/ea9e999e6553ccfa0f3a5ac00ba042ca7e0bfc78))
* add workflow to populate processing api with new sequencing runs ([0117935](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/0117935c16a6d8496952aa95f482e24831b6a546))
* add workflow to upload analysis files ([c1d9482](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/c1d9482b4316c4c581f863558c561ae191bcf7e2))
* convert processing workflows into one workflow and clean up ([2bbf541](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/2bbf54105fc85fe105ec9b84d68c110bd038868e))
* make it possible to specify files/folders to sync that may exist ([3296db1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/3296db1dd572ba75a164556b32bd116e1f3ea8e4))
* new workflow to prepare for processing ([85c506c](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/85c506cd0d2c0160ad9a752f7e1ea51b1294a3fc))
* remove inherited transfer from wp2 tm ([c0087b2](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/c0087b21699cf5c8e4ac492ef46debe179c5f673))
* remove workflows that aren't used anymore ([e057219](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/e057219a334c637dea985dfdbe1bd94dab2dd0c4))
* rename variables ([5ac5e2e](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/5ac5e2ed1343e0f26f8d20e7c102b05c5c157532))
* simplify preprocessong workflow ([e343f40](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/e343f40e5da8136d0462b0b352fff0310b56512a))
* simplify result retrieval ([f031d8c](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/f031d8c9aac5ea336bf5fb14b493dd2d926a01aa))
* update archive workflow for sequence files ([824114a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/824114a45f6ffe04f691d1e1cd0640815ab14412))
* update archiving ([6caac12](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/6caac12f8636af8e9502d8482098f658042eec8c))
* update process workspace to talk with processing api ([b7fd65a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/b7fd65a61a815252fec6f19cb14c6e555d0598e7))


### Bug Fixes

* add Api-key to archive sensor ([3fa8916](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/3fa8916f19756bc9facd661c8a82d6530fb58afe))
* change scratch path ([6a22a54](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/6a22a542f786d1331a346ecf5d67b235ca1a3024))
* fix bug and clean up ([914a4b6](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/914a4b61787dc469ce7550f693681f7cfdd1b0b7))
* look for new outbox path ([59cf1c7](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/59cf1c75863b7260fe9010c3ab53774cd2087668))
* make sure core.local run using bash shell ([cac8c4e](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/cac8c4efa5ca577aa4773c839a0de680b8577902))
* minor bug fixes ([c65df42](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/c65df4233f2b3fffb4018557cfc19c9719e00ab2))

### [2.13.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.13.0...v2.13.1) (2023-09-05)


### Bug Fixes

* generate_elastic_statistics for te ([4951c4f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4951c4fd2b492dbefa5e714483f2e2f90824afd8))

## [2.13.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.12.3...v2.13.0) (2023-06-27)


### Features

* add semantic git commit check ([c1a9306](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/c1a93069eed2b02d8bf46e3d610d5046d498699d))
* bump version for hastings and semantic check ([55215ca](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/55215ca5f75095e24103e7f224cf5cd01cf0ce72))

### [2.12.3](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.12.2...v2.12.3) (2023-05-30)


### Bug Fixes

* fix header wp3 retrieve ([246d5f5](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/246d5f50876e04d6dfa49f9e82bc88d99dcebd4a))

### [2.12.2](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.12.1...v2.12.2) (2023-05-30)


### Bug Fixes

* fix wp2 mail subject ([dedf408](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/dedf408fdb59c07acc9297465787f3dba40bc388))

### [2.12.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.12.0...v2.12.1) (2023-05-30)


### Bug Fixes

* fix subject ([fbcb114](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/fbcb11409e7219d2941ffc3d233efb84f70d6979))

## [2.12.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.11.1...v2.12.0) (2023-05-30)


### Features

* update version number ([7156d2b](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/7156d2be0a8b7bd51e5d41de0a115b6245aaf1d9))

### [2.11.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.11.0...v2.11.1) (2023-04-27)


### Bug Fixes

* hande failing archiving due to multiple workflows archiving the same data ([c60ef9e](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/c60ef9e995aa4b867835fcad186994c7b9142c44))

## [2.11.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.10.1...v2.11.0) (2023-04-21)


### Features

* create a file with archived runfolders ([f39b814](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/f39b81463c1504e442e1200c8c02e1e1bcb3e648))
* transfer RunParameters.xml and RunInfo.xml files to compute ([dd5f5be](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/dd5f5beea8a2be38834768c231d83151d9f42482))
* wp2 abl pipeline add ([37efb29](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/37efb29de88c92f1425519edeb0ef1839f0fb0d8))

### [2.10.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.10.0...v2.10.1) (2023-03-10)


### Bug Fixes

* update version ([51d987a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/51d987adc3f55c5455af16cbb34cfc51e1916c64))

## [2.10.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.9.1...v2.10.0) (2023-03-08)


### Features

* print run statistics for gms560 ([1cadf8b](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/1cadf8bfaf20613dd3a01aef1ea8fce9df6e846b))


### Bug Fixes

* removed SERA from result retrieved message ([b64bd94](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/b64bd94d23b4de7be774fdd72e00d2118b8c2aab))
* send mail when GMS560 is started and not when it' done ([4a9e7d1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4a9e7d1c3b58f14513b62c8b901b3f7d12c1bd34))

### [2.9.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.9.0...v2.9.1) (2023-02-08)


### Documentation

* update config ([860aa5d](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/860aa5def41c7632ad4befa097138fe0d8744e4a))
* update config ([6e21613](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/6e216139a54b7a04ed8de0bf0e8817c81850a3e3))

## [2.9.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.8.2...v2.9.0) (2023-02-08)


### Features

* add checkout for gms560 config repo ([fa50a8f](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/fa50a8f747363dacf27c92b9faea245d82fdaa36))


### Bug Fixes

* correct indentation error ([bc866e5](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/bc866e5174ff12b14b71ad199b3e279b3d07c4e0))

### [2.8.2](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.8.1...v2.8.2) (2023-02-06)


### Bug Fixes

* change to use correct variable fo fetching experiment ([4553191](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4553191bc9082922379807f1661887ecac41865b))


### Documentation

* update version ([56f2217](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/56f22170d2f8d47c76371f9fa04294a649c15b9c))

### [2.8.1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.8.0...v2.8.1) (2023-02-03)


### Bug Fixes

* make it possible to set num checks and delay for demultipling queries ([79b12e3](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/79b12e37aadf33b943140e163c68a286cd5dace5))


### Documentation

* update pack version ([6f45394](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/6f453943d2ba344d1ae1994bd928f7958e8ce739))

## [2.8.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.7.0...v2.8.0) (2023-01-24)


### Features

* add demultiplexing using service ([0d96c34](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/0d96c34774dd2b97ba3790fa2f5721ecd5f08926))
* added GMS560 workflow, rules, and actions ([4c3a383](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4c3a3838aafed218b765b8af17986e306cdfdff7))
* added start of workflow for hospital ([5c9cc94](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/5c9cc94ee28bf191b5b8eece47a6bf0f3afd7ea7))
* cp fastq filer to analysis folder ([5944cf1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/5944cf100c53eb2b0fa947febfb4ac52d7ccebda))
* put fastq files in folder unique to workflow and remove files after archiving ([922dc7a](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/922dc7aa5440c9b84bd70f9e8961d61680198622))
* update compressed runfolder name ([afb210d](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/afb210d811eb7b15d914029cb5872013436212d6))
* **wp1:** fetach rna and dna to same result folder ([139c210](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/139c210fa736a8be41acc6bc02219b5fcaa087b5))


### Bug Fixes

* add missing task GMS560_analysis_started ([f216fb1](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/f216fb1348851d1ee941cc8a075f3a16aa331731))
* added default values ([c50a0bb](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/c50a0bbd21567390bdad297edcf5b0a24dcbf382))
* added GMS560 git url ([b3a4ef9](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/b3a4ef99bb5f681016cf366779a4cb940dd373e4))
* changed run names to prefix_GMS560 ([6cc9731](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/6cc9731951ec697db308a60c3006b9686a8a6f69))
* remove default for wp1 GMS560 pipeline ([08c23ed](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/08c23edbe768500fdb93afa43250b9c1c86a249c))
* rm input folder as current directory is used ([5efb3e3](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/5efb3e3b86942f39cfaee35fbdea2f627d268dfe))

## [2.7.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.6.0...v2.7.0) (2022-05-16)


### Features

* update version ([4bcf2cf](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/4bcf2cfc77b11d4290d5e285972c34ddbacb688a))

## [2.6.0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/compare/v2.5.0...v2.6.0) (2022-05-09)


### Features

* add CODEOWNERS ([0dbd6ee](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/0dbd6ee4fd987b364057fe83a30af40c9122f3f3))
* add release please workflow ([8d23be0](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/8d23be02dee888771b0b07a3cc363aacf6af1ee5))
* limit number of jobs running at same time to 4 ([b6cd668](https://www.github.com/clinical-genomics-uppsala/ductus-packs/commit/b6cd6680c57ea3de37d2ae2dd937a80d11480288))
